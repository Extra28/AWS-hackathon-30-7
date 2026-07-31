"""Hourly simulation, ad hoc trip handling, baselines, metrics, validation.

Covers Requirements 9, 11, 12 and 13.

All policies are driven through identical demand and fault realisations so the
comparison is not confounded by differing random draws (Req 12.10).
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from . import db, forecast, scheduler
from .generate import FAULT_CATEGORIES, notes_per_cartridge_from_amount

POLICIES = ("ai_planner", "reactive", "fixed_calendar")
POLICY_LABELS = {
    "ai_planner": "AI planner (forecast-driven)",
    "reactive": "Reactive (refill when below 25%)",
    "fixed_calendar": "Fixed calendar (every N days)",
}


def build_fault_schedule(cfg, fleet, sim_days, rng) -> list[dict]:
    """Pre-draw faults once so every policy faces the same failures."""
    out = []
    for t in range(sim_days):
        onsets = rng.random(fleet["n"]) < fleet["fault_prop"]
        for i in np.nonzero(onsets)[0]:
            cat, dis = FAULT_CATEGORIES[int(rng.integers(len(FAULT_CATEGORIES)))]
            sh = int(rng.integers(0, 24))
            dur = int(rng.integers(2, 19))
            start = t * 24 + sh
            out.append({
                "atm": int(i), "category": cat, "disables": int(dis),
                "start_h": start, "detected_h": start + int(rng.integers(0, 3)),
                "end_h": start + dur,
            })
    return out


def run_policy(cfg, fleet, bundle, A_hist, latent_future, faults, start_notes,
               last_refill_offset, policy, sim_start_date, verbose=False) -> dict:
    """Step one policy hourly across the simulation window."""
    n, n_cart = fleet["n"], fleet["n_cart"]
    note_cap = fleet["note_cap"]
    cap = fleet["capacity"]
    low_pct = float(cfg["low_cash_threshold_pct"])
    elig_pct = float(cfg["refill_eligibility_pct"])
    low_level = cap * low_pct
    sim_days = int(cfg["sim_days"])
    horizon = int(cfg["planning_horizon_days"])
    trip_cap = int(cfg["daily_trip_cap"])
    max_gap = int(cfg["max_refill_gap_days"])
    lag = int(cfg["adhoc_response_lag_hours"])
    interval = int(cfg["fixed_calendar_interval_days"])
    qs = bundle["quantiles"]
    kq = qs.index(float(cfg["safety_quantile"]))
    win_hour = {b: cfg.window_start_hour(b) for b in scheduler.BANDS}

    notes = start_notes.copy()
    A = A_hist.copy()
    last_refill = last_refill_offset.copy().astype(float)  # sim-day index, may be < 0

    fault_by_hour: dict[int, list] = {}
    for f in faults:
        fault_by_hour.setdefault(f["detected_h"], []).append(f)
    disable_until = np.zeros(n)
    disabled = np.zeros(n, dtype=bool)

    trips = []
    pending: dict[int, dict] = {}
    stockouts = 0
    breach_hours = 0
    unmet_total = 0.0
    balance_sum = 0.0
    balance_obs = 0
    fill_at_refill = []
    intervals = []
    per_day_trips = np.zeros(sim_days, dtype=int)
    per_day_bands = {b: np.zeros(sim_days, dtype=int) for b in scheduler.BANDS}
    refilled_today = np.zeros(n, dtype=bool)

    for t in range(sim_days):
        day_date = sim_start_date + dt.timedelta(days=t)
        refilled_today[:] = False

        # ---- decide the day's planned refills -------------------------------
        planned: dict[int, str] = {}
        if policy == "ai_planner":
            as_of_j = A.shape[1] - 1
            as_of_date = day_date - dt.timedelta(days=1)
            preds = forecast.predict(bundle, A, as_of_j, horizon)
            fq = preds[:, :, kq]
            lr_dates = [day_date - dt.timedelta(days=int(t - last_refill[i]))
                        for i in range(n)]
            windows = scheduler.compute_windows(cfg, fleet, notes, lr_dates, fq,
                                                as_of_date)
            plan = scheduler.build_schedule(cfg, fleet, windows, fq, as_of_date)
            for aid, a in plan["assigned"].items():
                if a["day_k"] == 1:
                    planned[a["win"]["idx"]] = a.get("band", "P0")
        elif policy == "fixed_calendar":
            due = np.nonzero((t - last_refill) >= interval)[0]
            order = sorted(due, key=lambda i: last_refill[i])
            for rank, i in enumerate(order):
                planned[int(i)] = scheduler.BANDS[0] if rank < 0.65 * len(order) else (
                    scheduler.BANDS[1] if rank < 0.9 * len(order) else scheduler.BANDS[2])
        # reactive: nothing planned; the 15-day rule below still forces visits.

        forced = np.nonzero((t - last_refill) >= max_gap)[0]
        for i in forced:
            planned.setdefault(int(i), "P0")

        # Respect the daily cap on the planned portion.
        if len(planned) > trip_cap:
            keep = sorted(planned.keys(), key=lambda i: last_refill[i])[:trip_cap]
            planned = {i: planned[i] for i in keep}

        day_want = notes_per_cartridge_from_amount(fleet, latent_future[:, t])
        day_served = np.zeros(n)
        stockout_today = np.zeros(n, dtype=bool)

        for h in range(24):
            abs_h = t * 24 + h

            for f in fault_by_hour.get(abs_h, []):
                i = f["atm"]
                if f["disables"]:
                    disabled[i] = True
                    disable_until[i] = f["end_h"]
                    if i not in pending and not refilled_today[i]:
                        pending[i] = {"attend_h": abs_h + lag, "trigger": "fault"}

            recovered = disabled & (disable_until <= abs_h)
            disabled[recovered] = False

            # Scheduled refills land at their window's start hour (Req 12.3).
            for i, band in list(planned.items()):
                if win_hour[band] == h and not refilled_today[i]:
                    if per_day_trips[t] >= trip_cap:
                        continue
                    bal = float((notes[i, :] * fleet["denoms"][i, :]).sum())
                    fill_at_refill.append(bal / cap[i])
                    notes[i, :] = note_cap
                    refilled_today[i] = True
                    if last_refill[i] > -1e8:
                        intervals.append(t - last_refill[i])
                    last_refill[i] = t
                    per_day_trips[t] += 1
                    per_day_bands[band][t] += 1
                    trips.append((policy, fleet["atm_ids"][i], day_date.isoformat(),
                                  band, "planned", "schedule",
                                  float(note_cap * fleet["denoms"][i, :].sum() - bal),
                                  bal / cap[i]))
                    pending.pop(i, None)

            # ---- demand for this hour ---------------------------------------
            frac = fleet["hour_profile"][:, h]
            want = day_want * frac[:, None]
            want[disabled, :] = 0.0

            served = np.minimum(notes, want)
            short = want - served
            # A cartridge that cannot meet live demand is a stockout for that ATM.
            stockout_today |= ((short > 1e-6) & (want > 1e-6)).any(axis=1)
            notes -= served

            served_amt = (served * fleet["denoms"]).sum(axis=1)
            day_served += served_amt
            unmet_total += float(((want - served) * fleet["denoms"]).sum())
            blocked_amt = (day_want * frac[:, None] * fleet["denoms"]).sum(axis=1)
            unmet_total += float(blocked_amt[disabled].sum())

            total = (notes * fleet["denoms"]).sum(axis=1)
            balance_sum += float(total.sum())
            balance_obs += 1
            below = total < low_level
            breach_hours += int(below.sum())

            # Ad hoc raise on a low-cash breach (Req 9.1).
            for i in np.nonzero(below)[0]:
                if i in pending or refilled_today[i]:
                    continue
                pending[int(i)] = {"attend_h": abs_h + lag, "trigger": "low_cash"}

            # Ad hoc attendance after the response lag (Req 9.4).
            for i, info in list(pending.items()):
                if info["attend_h"] > abs_h:
                    continue
                if refilled_today[i] or per_day_trips[t] >= trip_cap:
                    continue
                bal = float((notes[i, :] * fleet["denoms"][i, :]).sum())
                pct = bal / cap[i]
                load_cash = info["trigger"] == "low_cash" or pct < elig_pct
                if load_cash:
                    fill_at_refill.append(pct)
                    notes[i, :] = note_cap
                    if last_refill[i] > -1e8:
                        intervals.append(t - last_refill[i])
                    last_refill[i] = t          # Req 9.5
                refilled_today[i] = True
                per_day_trips[t] += 1
                trips.append((policy, fleet["atm_ids"][i], day_date.isoformat(),
                              None, "adhoc", info["trigger"],
                              float(note_cap * fleet["denoms"][i, :].sum() - bal)
                              if load_cash else 0.0, pct))
                pending.pop(i, None)

            if h == 23:
                # Append the whole day's served demand so the model sees real history.
                A = np.concatenate([A, day_served[:, None]], axis=1)
                stockouts += int(stockout_today.sum())

    n_planned = sum(1 for r in trips if r[4] == "planned")
    n_adhoc = sum(1 for r in trips if r[4] == "adhoc")
    n_low = sum(1 for r in trips if r[5] == "low_cash")
    n_fault = sum(1 for r in trips if r[5] == "fault")
    fills = np.array(fill_at_refill) if fill_at_refill else np.array([0.0])
    ivs = np.array(intervals) if intervals else np.array([0.0])
    in_band = float(np.mean((fills >= low_pct) & (fills <= elig_pct)))

    metrics = {
        "total_trips": float(len(trips)),
        "planned_trips": float(n_planned),
        "adhoc_trips": float(n_adhoc),
        "adhoc_low_cash": float(n_low),
        "adhoc_fault": float(n_fault),
        "adhoc_rate_pct": float(100.0 * n_adhoc / max(len(trips), 1)),
        "mean_trips_per_day": float(per_day_trips.mean()),
        "peak_trips_per_day": float(per_day_trips.max()),
        "days_over_cap": float(int((per_day_trips > trip_cap).sum())),
        "stockout_events": float(stockouts),
        "low_cash_breach_hours": float(breach_hours),
        "unmet_demand": float(unmet_total),
        "mean_idle_cash": float(balance_sum / max(balance_obs, 1)),
        "mean_fill_at_refill_pct": float(100.0 * fills.mean()),
        "refills_inside_band_pct": float(100.0 * in_band),
        "mean_refill_interval_days": float(ivs.mean()),
        "max_refill_interval_days": float(ivs.max()),
        "intervals_over_limit": float(int((ivs > max_gap).sum())),
    }
    for b in scheduler.BANDS:
        tot = sum(per_day_bands[x].sum() for x in scheduler.BANDS)
        metrics["planned_mix_%s_pct" % b] = float(
            100.0 * per_day_bands[b].sum() / max(tot, 1))
    return {"metrics": metrics, "trips": trips, "per_day": per_day_trips.tolist()}


def run_all(conn, cfg, fleet, bundle, hist, verbose=True) -> dict:
    """Run every policy over shared draws and persist the comparison."""
    sim_days = int(cfg["sim_days"])
    hist_days = int(cfg["history_days"])
    latent = hist["latent"]
    dates = hist["dates"]
    today = hist["today"]

    _, _, A_all, _ = forecast.load_history(conn)
    A_hist = A_all[:, : hist_days + 1]
    latent_future = latent[:, hist_days + 1: hist_days + 1 + sim_days]
    if latent_future.shape[1] < sim_days:
        pad = sim_days - latent_future.shape[1]
        latent_future = np.pad(latent_future, ((0, 0), (0, pad)), mode="edge")

    state = db.query(conn, "SELECT * FROM atm_state ORDER BY atm_id")
    start_notes = np.array([db.json_loads(r["cartridge_notes"]) for r in state],
                           dtype=float)
    last_refill_offset = np.array([
        (dt.date.fromisoformat(r["last_refill_date"]) - today).days for r in state
    ], dtype=float)

    fault_rng = np.random.default_rng(int(cfg["seed"]) + 777)
    faults = build_fault_schedule(cfg, fleet, sim_days, fault_rng)

    conn.execute("DELETE FROM sim_trips")
    conn.execute("DELETE FROM sim_metrics")
    results = {}
    for policy in POLICIES:
        if verbose:
            print("    simulating %s ..." % policy)
        res = run_policy(cfg, fleet, bundle, A_hist, latent_future, faults,
                         start_notes, last_refill_offset, policy, today)
        results[policy] = res
        conn.executemany(
            "INSERT INTO sim_trips(policy,atm_id,d,priority_window,trip_type,trigger,"
            "cash_loaded,pct_before) VALUES (?,?,?,?,?,?,?,?)", res["trips"])
        for k, v in res["metrics"].items():
            db.set_metric(conn, policy, k, value=v)
        db.set_metric(conn, policy, "label", text_value=POLICY_LABELS[policy])

    # Improvement of the planner against each baseline (Req 13.2, 13.5).
    ai = results["ai_planner"]["metrics"]
    for base in ("reactive", "fixed_calendar"):
        bm = results[base]["metrics"]
        for key, label in (("total_trips", "trips"), ("adhoc_trips", "adhoc")):
            delta = bm[key] - ai[key]
            pct = 100.0 * delta / bm[key] if bm[key] else 0.0
            db.set_metric(conn, "ai_planner", "%s_reduction_vs_%s" % (label, base), delta)
            db.set_metric(conn, "ai_planner",
                          "%s_reduction_pct_vs_%s" % (label, base), pct)
    conn.commit()
    return results


# -------------------------------------------------------------- Requirement 11
def validate_schedule(conn, cfg, run_id: int) -> list[dict]:
    """Re-read the stored schedule from SQL and check it independently."""
    conn.execute("DELETE FROM validation_results WHERE run_id=?", (run_id,))
    cap = int(cfg["daily_trip_cap"])
    eff_cap = int(cfg.effective_daily_cap)
    max_gap = int(cfg["max_refill_gap_days"])
    checks = []

    per_day = db.query(
        conn, "SELECT plan_date, COUNT(*) c FROM schedule WHERE run_id=? "
              "GROUP BY plan_date ORDER BY plan_date", (run_id,))
    worst = max((r["c"] for r in per_day), default=0)
    checks.append(("daily_trip_cap", worst <= cap, float(worst), float(cap),
                   "peak planned visits on a single day"))
    checks.append(("planned_cap_with_adhoc_reserve", worst <= eff_cap, float(worst),
                   float(eff_cap),
                   "peak planned visits against cap net of ad hoc reserve"))

    dupes = db.query(
        conn, "SELECT atm_id, plan_date, COUNT(*) c FROM schedule WHERE run_id=? "
              "GROUP BY atm_id, plan_date HAVING c > 1", (run_id,))
    checks.append(("one_visit_per_atm_per_day", len(dupes) == 0, float(len(dupes)),
                   0.0, "ATM/date pairs scheduled more than once"))

    # Refill window adherence (Req 11.2) and the 55% rule (Req 11.3).
    rows = db.query(
        conn,
        "SELECT s.atm_id, s.plan_date, s.pct_at_visit, s.compliance_driven, "
        "w.opens_date, w.effective_latest FROM schedule s "
        "JOIN refill_windows w ON w.atm_id = s.atm_id "
        "WHERE s.run_id = ? AND w.as_of = (SELECT as_of FROM plan_runs WHERE run_id=?)",
        (run_id, run_id))
    late = sum(1 for r in rows if r["effective_latest"] and r["plan_date"] > r["effective_latest"])
    early = sum(1 for r in rows if r["opens_date"] and r["plan_date"] < r["opens_date"])
    checks.append(("visit_before_deadline", late == 0, float(late), 0.0,
                   "visits scheduled after the effective latest date"))
    checks.append(("visit_not_before_window_opens", early == 0, float(early), 0.0,
                   "visits scheduled before the 55% eligibility date"))

    above = sum(1 for r in rows
                if (r["pct_at_visit"] or 0) > float(cfg["refill_eligibility_pct"])
                and not r["compliance_driven"])
    checks.append(("no_wasteful_refill_above_55pct", above == 0, float(above), 0.0,
                   "non-compliance visits to ATMs above 55% of capacity"))

    gaps = db.query(
        conn,
        "SELECT s.atm_id, julianday(s.plan_date) - julianday(a.last_refill_date) g "
        "FROM schedule s JOIN atm_state a ON a.atm_id = s.atm_id WHERE s.run_id=?",
        (run_id,))
    bad_gap = sum(1 for r in gaps if (r["g"] or 0) > max_gap)
    checks.append(("max_refill_gap", bad_gap == 0, float(bad_gap), 0.0,
                   "scheduled visits leaving a gap longer than the limit"))

    # Priority mix per day, largest-remainder targets.
    mix_bad = []
    for r in per_day:
        got = db.query(
            conn, "SELECT priority_window b, COUNT(*) c FROM schedule "
                  "WHERE run_id=? AND plan_date=? GROUP BY priority_window",
            (run_id, r["plan_date"]))
        actual = {x["b"]: x["c"] for x in got}
        target = scheduler.largest_remainder(r["c"], cfg["priority_mix"])
        if any(actual.get(b, 0) != target[b] for b in scheduler.BANDS):
            mix_bad.append(r["plan_date"])
    checks.append(("daily_priority_mix", len(mix_bad) == 0, float(len(mix_bad)),
                   0.0, "days whose band split differs from the 65/25/10 target"))

    out = []
    for name, passed, observed, limit, detail in checks:
        conn.execute(
            "INSERT INTO validation_results(run_id,check_name,passed,observed,"
            "limit_val,detail) VALUES (?,?,?,?,?,?)",
            (run_id, name, int(bool(passed)), observed, limit, detail))
        out.append({"check_name": name, "passed": bool(passed), "observed": observed,
                    "limit_val": limit, "detail": detail})
    conn.commit()
    return out
