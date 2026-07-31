"""Refill windows, schedule construction, priority windows, load sizing.

Covers Requirements 6, 7, 8 and 10.

The scheduler is a deterministic greedy construction with a load-relief pass.
Constraints are treated as strong preferences rather than hard guarantees at
prototype scale; whatever deviation remains is measured and reported by the
validator instead of being hidden.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from . import db, forecast
from .generate import DENOMS, notes_per_cartridge_from_amount

BANDS = ("P0", "P1", "P2")


# --------------------------------------------------------------- Requirement 6
def compute_windows(cfg, fleet, notes, last_refill, fq, as_of) -> list[dict]:
    """Derive each ATM's refill window from its balance and demand forecast.

    fq is the per-day forecast at the safety quantile, shaped (n_atms, horizon).
    """
    n, n_cart = fleet["n"], fleet["n_cart"]
    horizon = fq.shape[1]
    cap = fleet["capacity"]
    elig_pct = float(cfg["refill_eligibility_pct"])
    low_pct = float(cfg["low_cash_threshold_pct"])
    max_gap = int(cfg["max_refill_gap_days"])
    note_cap = fleet["note_cap"]

    elig_level = cap * elig_pct
    low_level = cap * low_pct
    balance = (notes * fleet["denoms"]).sum(axis=1)

    cum = np.cumsum(fq, axis=1)
    traj = balance[:, None] - cum                      # balance at end of day k+1

    # Per-cartridge depletion (Req 6.5).
    cart_notes = np.zeros((n, n_cart, horizon))
    running = notes.copy()
    for k in range(horizon):
        want = notes_per_cartridge_from_amount(fleet, fq[:, k])
        running = np.maximum(running - want, 0.0)
        cart_notes[:, :, k] = running

    def first_below(arr, level):
        # level may be per-ATM; reshape to a column so it broadcasts over days.
        lv = np.asarray(level, dtype=float)
        if lv.ndim == 1:
            lv = lv[:, None]
        hit = arr < lv
        idx = np.argmax(hit, axis=1)
        return np.where(hit.any(axis=1), idx + 1, -1)

    # traj[:, m] is the balance at the END of day m+1. For the window to open we
    # want the first day that STARTS below eligibility, hence the +1 shift.
    # For the closing deadline the end-of-day crossing is the correct test: if the
    # balance ends day c below the floor, the refill must land on or before day c.
    opens_raw = first_below(traj, elig_level)
    opens_k = np.where(opens_raw >= 1, opens_raw + 1, -1)
    closes_k = first_below(traj, low_level)
    dep_k = first_below(traj, 0.0)

    cart_low = (cart_notes < note_cap * low_pct).any(axis=1)
    cart_low_k = np.where(cart_low.any(axis=1), np.argmax(cart_low, axis=1) + 1, -1)

    out = []
    for i in range(n):
        pct_now = float(balance[i] / cap[i])
        o = 0 if pct_now < elig_pct else int(opens_k[i])
        c = int(closes_k[i])
        if pct_now < low_pct:
            c = 0
        cl = int(cart_low_k[i])
        # A cartridge starving is at least as bad as the aggregate floor.
        if cl >= 0 and (c < 0 or cl < c):
            c, cart_driver = cl, True
        else:
            cart_driver = False

        opens_date = as_of + dt.timedelta(days=o) if o >= 0 else None
        closes_date = as_of + dt.timedelta(days=c) if c >= 0 else None
        dep_date = as_of + dt.timedelta(days=int(dep_k[i])) if dep_k[i] >= 0 else None

        lr = last_refill[i]
        reg = lr + dt.timedelta(days=max_gap)

        if closes_date is None:
            eff, driver = reg, "compliance"
        elif reg <= closes_date:
            eff, driver = reg, "compliance"
        else:
            eff, driver = closes_date, "demand"

        low_value = 1 if (opens_date is not None and reg < opens_date) else 0
        eff_start = max(as_of, opens_date or as_of)
        slack = (eff - eff_start).days

        # Hour within the closing day at which the floor is crossed (Req 6.10).
        closes_hour = None
        if closes_date is not None and c >= 1:
            prev = float(traj[i, c - 2]) if c >= 2 else float(balance[i])
            day_dem = float(fq[i, c - 1]) if c - 1 < horizon else 0.0
            if day_dem > 0:
                need = max(prev - low_level[i], 0.0) / day_dem
                cumh = np.cumsum(fleet["hour_profile"][i, :])
                closes_hour = int(np.searchsorted(cumh, min(need, 0.999)))

        out.append({
            "atm_id": fleet["atm_ids"][i],
            "idx": i,
            "pct_now": pct_now,
            "balance": float(balance[i]),
            "opens_date": opens_date.isoformat() if opens_date else None,
            "opens_k": o,
            "closes_date": closes_date.isoformat() if closes_date else None,
            "closes_k": c,
            "closes_hour": closes_hour,
            "depletion_date": dep_date.isoformat() if dep_date else None,
            "regulatory_deadline": reg.isoformat(),
            "effective_latest": eff.isoformat(),
            "effective_latest_k": (eff - as_of).days,
            "driver": driver,
            "cartridge_driven": cart_driver,
            "compliance_low_value": low_value,
            "slack_days": int(slack),
        })
    return out


def store_windows(conn, windows, as_of) -> None:
    conn.execute("DELETE FROM refill_windows WHERE as_of=?", (as_of.isoformat(),))
    conn.executemany(
        "INSERT INTO refill_windows(atm_id,as_of,pct_now,opens_date,closes_date,"
        "closes_hour,depletion_date,regulatory_deadline,effective_latest,driver,"
        "compliance_low_value,slack_days) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(w["atm_id"], as_of.isoformat(), w["pct_now"], w["opens_date"],
          w["closes_date"], w["closes_hour"], w["depletion_date"],
          w["regulatory_deadline"], w["effective_latest"], w["driver"],
          w["compliance_low_value"], w["slack_days"]) for w in windows])
    conn.commit()


# ------------------------------------------------------------ Requirement 7, 8
def largest_remainder(total: int, shares: dict) -> dict:
    """Apportion an integer total across bands (Req 8.2)."""
    raw = {b: total * float(shares[b]) for b in BANDS}
    base = {b: int(np.floor(raw[b])) for b in BANDS}
    left = total - sum(base.values())
    order = sorted(BANDS, key=lambda b: (-(raw[b] - base[b]), b))
    for k in range(left):
        base[order[k % len(order)]] += 1
    return base


def build_schedule(cfg, fleet, windows, fq, as_of) -> dict:
    """Assign each ATM a plan date and priority window over the horizon."""
    horizon = int(cfg["planning_horizon_days"])
    eff_cap = int(cfg.effective_daily_cap)
    w = cfg["objective_weights"]
    cap = fleet["capacity"]
    low_pct = float(cfg["low_cash_threshold_pct"])

    cum = np.cumsum(fq, axis=1)
    balance = np.array([x["balance"] for x in windows])

    def pct_at(i, k):
        # Balance at the *start* of plan day k (demand through day k-1 consumed).
        used = cum[i, k - 2] if k >= 2 else 0.0
        return max(balance[i] - used, 0.0) / cap[i]

    # Least slack first, then earliest deadline.
    order = sorted(windows, key=lambda x: (x["slack_days"], x["effective_latest_k"],
                                           x["atm_id"]))
    load = {k: 0 for k in range(1, horizon + 1)}
    assigned: dict[str, dict] = {}

    for win in order:
        i = win["idx"]
        lo = max(1, win["opens_k"] if win["opens_k"] >= 0 else 1)
        hi = win["effective_latest_k"]
        overdue = hi < 1
        if overdue:
            lo, hi = 1, 1
        hi = min(max(hi, lo), horizon)
        lo = min(lo, hi)

        best_d, best_cost = None, None
        for d in range(lo, hi + 1):
            pct = pct_at(i, d)
            idle = float(w["idle_cash"]) * pct
            smooth = float(w["load_smoothing"]) * (load[d] / max(eff_cap, 1)) ** 2
            edge = 0.0
            ck = win["closes_k"]
            if ck >= 0:
                if d > ck:
                    edge = float(w["edge_risk"]) * (1.0 + 0.5 * (d - ck))
                elif d == ck:
                    edge = float(w["edge_risk"]) * 0.5
            cost = idle + smooth + edge
            if best_cost is None or cost < best_cost - 1e-12:
                best_cost, best_d = cost, d

        d = best_d if best_d is not None else lo
        load[d] += 1
        assigned[win["atm_id"]] = {
            "win": win, "day_k": d, "overdue": overdue,
            "pct_at_visit": pct_at(i, d),
        }

    # ---- relief pass: shed the most slack-rich ATMs off overloaded days ------
    for _ in range(6):
        over = [d for d in range(1, horizon + 1) if load[d] > eff_cap]
        if not over:
            break
        for d in over:
            movers = [a for a in assigned.values() if a["day_k"] == d]
            movers.sort(key=lambda a: -a["win"]["slack_days"])
            excess = load[d] - eff_cap
            for a in movers[:excess]:
                wi = a["win"]
                lo = max(1, wi["opens_k"] if wi["opens_k"] >= 0 else 1)
                hi = min(max(wi["effective_latest_k"], lo), horizon)
                cands = [x for x in range(lo, hi + 1) if x != d and load[x] < eff_cap]
                if not cands:
                    continue
                nd = min(cands, key=lambda x: (load[x], -x))
                load[d] -= 1
                load[nd] += 1
                a["day_k"] = nd
                a["pct_at_visit"] = pct_at(wi["idx"], nd)

    # ---- priority window assignment (Req 8) ---------------------------------
    deviations = []
    by_day: dict[int, list] = {}
    for a in assigned.values():
        by_day.setdefault(a["day_k"], []).append(a)

    for d, group in by_day.items():
        group.sort(key=lambda a: (a["win"]["slack_days"], a["pct_at_visit"],
                                  a["win"]["atm_id"]))
        quota = largest_remainder(len(group), cfg["priority_mix"])
        seq = []
        for b in BANDS:
            seq.extend([b] * quota[b])
        for a, band in zip(group, seq):
            a["band"] = band

        # Safety check: an ATM must survive until its window opens (Req 8.4).
        promoted = 0
        for a in group:
            i = a["win"]["idx"]
            k = a["day_k"]
            start_of_day = max(balance[i] - (cum[i, k - 2] if k >= 2 else 0.0), 0.0)
            day_dem = float(fq[i, k - 1])
            p0 = float(fleet["hour_profile"][i, 0:8].sum())
            p1 = float(fleet["hour_profile"][i, 8:12].sum())
            before = {"P0": 0.0, "P1": p0, "P2": p0 + p1}[a["band"]]
            bal_at_window = start_of_day - day_dem * before
            if bal_at_window < cap[i] * low_pct and a["band"] != "P0":
                a["band"] = "P0"
                promoted += 1
        if promoted:
            actual = {b: sum(1 for a in group if a["band"] == b) for b in BANDS}
            deviations.append({
                "plan_date": (as_of + dt.timedelta(days=d)).isoformat(),
                "reason": "promoted %d visit(s) to P0 for cash safety" % promoted,
                "quota": quota, "actual": actual,
            })

    return {"assigned": assigned, "load": load, "deviations": deviations,
            "horizon": horizon, "as_of": as_of}


# -------------------------------------------------------------- Requirement 10
def compute_loads(cfg, fleet, notes, plan, fq) -> dict:
    """Size the per-cartridge load for each scheduled visit."""
    note_cap = fleet["note_cap"]
    low_pct = float(cfg["low_cash_threshold_pct"])
    horizon = plan["horizon"]

    # Next planned visit per ATM, used to size cover.
    next_day = {aid: a["day_k"] for aid, a in plan["assigned"].items()}
    loads = {}
    for aid, a in plan["assigned"].items():
        i = a["win"]["idx"]
        k = a["day_k"]
        # Cover until the following visit; absent one, cover the rest of horizon.
        span_end = min(k + max(1, horizon - k), horizon)
        need_amt = float(fq[i, k - 1: span_end].sum())
        vec = np.zeros(fleet["n"])
        vec[i] = need_amt
        need_notes = notes_per_cartridge_from_amount(fleet, vec)[i]

        remaining = np.maximum(notes[i, :] - need_notes, 0.0)
        rows = []
        total_amt = 0.0
        for c in range(fleet["n_cart"]):
            floor_notes = note_cap * low_pct
            target = min(note_cap, floor_notes + float(need_notes[c]))
            load_notes = int(max(0.0, round(target - remaining[c])))
            amt = load_notes * int(fleet["denoms"][i, c])
            total_amt += amt
            rows.append((c, int(fleet["denoms"][i, c]), load_notes, float(amt)))
        loads[aid] = {"rows": rows, "total": total_amt}
    return loads


def recommend_denom_mix(conn, cfg, fleet, windows, fq) -> None:
    """Flag ATMs whose cartridge mix is the binding constraint (Req 10.5 - 10.7)."""
    conn.execute("DELETE FROM denom_recommendations")
    note_cap = fleet["note_cap"]
    rows = []
    for w in windows:
        if not w.get("cartridge_driven"):
            continue
        i = w["idx"]
        vec = np.zeros(fleet["n"])
        vec[i] = float(fq[i, :].mean())
        per_day = notes_per_cartridge_from_amount(fleet, vec)[i]
        if per_day.sum() <= 0:
            continue
        # Days of cover each cartridge provides today.
        cover = np.where(per_day > 0, note_cap / np.maximum(per_day, 1e-9), np.inf)
        scarce = int(np.argmin(cover))
        plentiful = int(np.argmax(cover))
        if scarce == plentiful:
            continue
        cur = [int(x) for x in fleet["denoms"][i, :]]
        rec = list(cur)
        rec[plentiful] = cur[scarce]
        cur_interval = float(np.min(cover))

        # Re-evaluate cover if the surplus cartridge is converted.
        counts = {}
        for d in rec:
            counts[d] = counts.get(d, 0) + 1
        share = np.zeros(fleet["n_cart"])
        for c, d in enumerate(rec):
            same = counts[d]
            k = DENOMS.index(d)
            share[c] = fleet["denom_value_share"][i, k] / same
        new_per_day = vec[i] * share / np.array([d for d in rec], dtype=float)
        new_cover = np.where(new_per_day > 0, note_cap / np.maximum(new_per_day, 1e-9),
                             np.inf)
        proj_interval = float(np.min(new_cover))
        if proj_interval <= cur_interval * 1.05:
            continue
        rows.append((
            w["atm_id"], db.json_dumps(cur), db.json_dumps(rec),
            round(cur_interval, 2), round(proj_interval, 2),
            "cartridge %d ($%d) starves first at %.1f days of cover; converting "
            "cartridge %d ($%d) to $%d extends the binding cover to %.1f days"
            % (scarce, cur[scarce], cur_interval, plentiful, cur[plentiful],
               cur[scarce], proj_interval),
        ))
    if rows:
        conn.executemany(
            "INSERT INTO denom_recommendations(atm_id,current_mix,recommended_mix,"
            "current_interval_days,projected_interval_days,rationale) "
            "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()


# ------------------------------------------------------------- reason / storage
def build_reason(a: dict) -> str:
    w = a["win"]
    pct = 100.0 * a["pct_at_visit"]
    if a["overdue"]:
        return ("Overdue: latest safe date %s already passed; scheduled at the "
                "earliest slot at %.0f%% of capacity." % (w["effective_latest"], pct))
    if w["compliance_low_value"]:
        return ("Compliance-driven: the %s-day rule falls due %s while the machine "
                "is still above the 55%% eligibility level (%.0f%% at visit). No cash "
                "need; this trip exists to satisfy the refill-interval policy."
                % (15, w["regulatory_deadline"], pct))
    if w["driver"] == "compliance":
        return ("Refill-interval rule binds first: due %s, ahead of the forecast "
                "low-cash date %s. Projected %.0f%% of capacity at visit."
                % (w["regulatory_deadline"], w["closes_date"] or "n/a", pct))
    extra = " A cartridge starves before the aggregate floor." if w["cartridge_driven"] else ""
    return ("Demand-driven: forecast (q%.0f) puts it below the 25%% floor by %s, so it "
            "is served inside its %s to %s window at %.0f%% of capacity.%s"
            % (90, w["closes_date"] or "n/a", w["opens_date"] or "now",
               w["effective_latest"], pct, extra))


def store_schedule(conn, cfg, plan, loads, as_of, seed) -> int:
    cur = conn.execute(
        "INSERT INTO plan_runs(created_at,as_of,seed,config_json,notes) "
        "VALUES (?,?,?,?,?)",
        (dt.datetime.now().isoformat(timespec="seconds"), as_of.isoformat(), seed,
         cfg.as_json(), db.json_dumps(plan["deviations"])))
    run_id = int(cur.lastrowid)

    for aid, a in plan["assigned"].items():
        w = a["win"]
        pd_ = (as_of + dt.timedelta(days=a["day_k"])).isoformat()
        ld = loads.get(aid, {"total": 0.0, "rows": []})
        c = conn.execute(
            "INSERT INTO schedule(run_id,atm_id,plan_date,priority_window,trip_type,"
            "compliance_driven,load_total,pct_at_visit,urgency,reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, aid, pd_, a.get("band", "P0"), "planned",
             int(w["compliance_low_value"]), ld["total"], a["pct_at_visit"],
             float(w["slack_days"]), build_reason(a)))
        sid = int(c.lastrowid)
        conn.executemany(
            "INSERT INTO schedule_loads(schedule_id,cartridge_idx,denomination,"
            "load_notes,load_amount) VALUES (?,?,?,?,?)",
            [(sid, r[0], r[1], r[2], r[3]) for r in ld["rows"]])
    conn.commit()
    return run_id


def plan_now(conn, cfg, fleet, bundle, A, as_of_j, as_of, notes, last_refill,
             seed=None) -> int:
    """End-to-end planning for a single as_of date. Returns the run id."""
    horizon = int(cfg["planning_horizon_days"])
    qs = bundle["quantiles"]
    kq = qs.index(float(cfg["safety_quantile"]))
    preds = forecast.predict(bundle, A, as_of_j, horizon)
    fq = preds[:, :, kq]

    windows = compute_windows(cfg, fleet, notes, last_refill, fq, as_of)
    store_windows(conn, windows, as_of)
    plan = build_schedule(cfg, fleet, windows, fq, as_of)
    loads = compute_loads(cfg, fleet, notes, plan, fq)
    recommend_denom_mix(conn, cfg, fleet, windows, fq)
    return store_schedule(conn, cfg, plan, loads, as_of, seed)
