"""Synthetic data generation: fleet, demand, hourly telemetry, faults.

Covers Requirements 1-4 at prototype scale.

The telemetry window is produced by running a reactive refill policy over the
generated demand, so the planner inherits a realistically messy fleet state
rather than an artificially tidy one.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from . import db

DENOMS = (10, 50, 100)

LOCATION_TYPES = ("shopping_mall", "office_district", "residential_estate",
                  "transit_hub", "tourist_area")
LOCATION_WEIGHTS = (0.22, 0.20, 0.28, 0.18, 0.12)

REGIONS = ("Central", "East", "West", "North", "North-East")

# Demand level multiplier by location type.
LOC_DEMAND_MULT = {
    "shopping_mall": 1.15,
    "office_district": 1.00,
    "residential_estate": 0.80,
    "transit_hub": 1.30,
    "tourist_area": 1.10,
}

# Cartridge denomination patterns, skewed by location value profile (Req 1.7).
LOC_CARTRIDGE_PATTERNS = {
    "residential_estate": [(10, 10, 50, 50), (10, 50, 50, 50), (10, 10, 10, 50)],
    "office_district": [(50, 50, 50, 100), (50, 50, 100, 100), (10, 50, 50, 100)],
    "shopping_mall": [(10, 50, 50, 100), (10, 10, 50, 100), (50, 50, 50, 100)],
    "transit_hub": [(10, 10, 50, 100), (10, 50, 50, 100), (10, 10, 50, 50)],
    "tourist_area": [(50, 100, 100, 100), (50, 50, 100, 100), (10, 50, 100, 100)],
}

# Share of withdrawn *value* by denomination, before restricting to the
# denominations a given ATM actually stocks.
LOC_DENOM_VALUE_SHARE = {
    "residential_estate": {10: 0.35, 50: 0.50, 100: 0.15},
    "office_district": {10: 0.10, 50: 0.45, 100: 0.45},
    "shopping_mall": {10: 0.20, 50: 0.50, 100: 0.30},
    "transit_hub": {10: 0.30, 50: 0.50, 100: 0.20},
    "tourist_area": {10: 0.10, 50: 0.35, 100: 0.55},
}

# Unnormalised hour-of-day demand shape (Req 3.3). Index = hour 0..23.
LOC_HOUR_PROFILE = {
    "office_district": [0.3, 0.2, 0.15, 0.15, 0.2, 0.4, 0.9, 1.8, 2.6, 2.2, 2.0, 2.6,
                        4.2, 4.0, 2.8, 2.4, 2.6, 3.8, 3.4, 2.2, 1.4, 0.9, 0.6, 0.4],
    "residential_estate": [0.4, 0.25, 0.2, 0.15, 0.2, 0.4, 0.9, 1.6, 1.9, 1.7, 1.6, 1.7,
                           2.0, 1.9, 1.8, 1.9, 2.3, 3.0, 3.8, 4.0, 3.4, 2.4, 1.4, 0.7],
    "shopping_mall": [0.2, 0.15, 0.1, 0.1, 0.1, 0.2, 0.4, 0.8, 1.2, 1.6, 2.4, 3.2,
                      3.8, 4.0, 3.8, 3.6, 3.6, 3.8, 4.0, 3.8, 3.2, 2.4, 1.4, 0.6],
    "transit_hub": [0.5, 0.3, 0.2, 0.2, 0.4, 1.2, 2.6, 4.0, 4.2, 3.0, 2.2, 2.0,
                    2.2, 2.1, 2.0, 2.1, 2.8, 4.0, 4.2, 3.4, 2.4, 1.6, 1.0, 0.7],
    "tourist_area": [0.5, 0.35, 0.25, 0.2, 0.2, 0.3, 0.6, 1.0, 1.6, 2.2, 2.8, 3.2,
                     3.4, 3.4, 3.2, 3.2, 3.2, 3.4, 3.4, 3.2, 2.8, 2.2, 1.4, 0.8],
}

# Monday=0 .. Sunday=6
DOW_FACTOR = np.array([0.95, 0.90, 0.94, 1.02, 1.28, 1.32, 1.05])

FAULT_CATEGORIES = [
    ("dispenser_jam", 1),
    ("cash_unit_error", 1),
    ("network_outage", 1),
    ("card_reader_fault", 0),
    ("receipt_printer", 0),
]

# Approximate Singapore public holidays. Dates for the lunar/Islamic festivals
# are approximate; they only need to be plausible for synthetic data.
HOLIDAYS = {
    "2025-01-01": "New Year", "2025-01-29": "CNY", "2025-01-30": "CNY",
    "2025-03-31": "Hari Raya Puasa", "2025-04-18": "Good Friday",
    "2025-05-01": "Labour Day", "2025-05-12": "Vesak", "2025-06-07": "Hari Raya Haji",
    "2025-08-09": "National Day", "2025-10-20": "Deepavali", "2025-12-25": "Christmas",
    "2026-01-01": "New Year", "2026-02-17": "CNY", "2026-02-18": "CNY",
    "2026-03-21": "Hari Raya Puasa", "2026-04-03": "Good Friday",
    "2026-05-01": "Labour Day", "2026-05-31": "Vesak", "2026-05-27": "Hari Raya Haji",
    "2026-08-09": "National Day", "2026-11-08": "Deepavali", "2026-12-25": "Christmas",
    "2027-01-01": "New Year", "2027-02-06": "CNY", "2027-02-07": "CNY",
    "2027-03-11": "Hari Raya Puasa", "2027-03-26": "Good Friday",
    "2027-05-01": "Labour Day", "2027-05-20": "Vesak", "2027-05-17": "Hari Raya Haji",
    "2027-08-09": "National Day", "2027-10-28": "Deepavali", "2027-12-25": "Christmas",
}
CNY_DATES = [d for d, n in HOLIDAYS.items() if n == "CNY"]


def _calendar_factor(day: dt.date) -> tuple[float, int]:
    """Return (demand multiplier, is_holiday) for a date (Req 3.4 - 3.6)."""
    key = day.isoformat()
    f = DOW_FACTOR[day.weekday()]
    is_hol = 1 if key in HOLIDAYS else 0

    # Payday effects: month end / start, and mid month.
    dom = day.day
    days_in_month = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_in_month = (days_in_month - dt.timedelta(days=1)).day
    if dom >= days_in_month - 1 or dom <= 2:
        f *= 1.35
    elif 14 <= dom <= 16:
        f *= 1.20

    if is_hol:
        f *= 1.25

    # Chinese New Year cash gifting spike in the week before (Req 3.6).
    for cny in CNY_DATES:
        cd = dt.date.fromisoformat(cny)
        delta = (cd - day).days
        if 0 <= delta <= 7:
            f *= 1.6 + 0.9 * (1.0 - delta / 7.0)
            break
    return float(f), is_hol


def _norm(vec) -> np.ndarray:
    a = np.asarray(vec, dtype=float)
    return a / a.sum()


def generate_fleet(conn, cfg, rng) -> dict:
    """Create the ATM fleet and its cartridge configuration (Req 1)."""
    n = int(cfg["n_atms"])
    note_cap = int(cfg["notes_per_cartridge"])
    n_cart = int(cfg["cartridges_per_atm"])
    elig_pct = float(cfg["refill_eligibility_pct"])
    low_pct = float(cfg["low_cash_threshold_pct"])

    loc_types = rng.choice(LOCATION_TYPES, size=n, p=LOCATION_WEIGHTS)
    regions = rng.choice(REGIONS, size=n)

    atm_rows, cart_rows = [], []
    denoms = np.zeros((n, n_cart), dtype=int)
    capacity = np.zeros(n)
    base_amt = np.zeros(n)
    fault_prop = np.zeros(n)

    for i in range(n):
        lt = str(loc_types[i])
        patterns = LOC_CARTRIDGE_PATTERNS[lt]
        pat = patterns[int(rng.integers(len(patterns)))]
        denoms[i, :] = pat[:n_cart]
        cap = float(sum(d * note_cap for d in denoms[i, :]))
        capacity[i] = cap

        # Daily turnover as a fraction of capacity. This range yields refill
        # intervals roughly between 6 and 25 days, so both the refill band and
        # the 15-day rule bite for different parts of the fleet.
        u = float(rng.uniform(0.030, 0.115)) * LOC_DEMAND_MULT[lt]
        base_amt[i] = cap * u
        fault_prop[i] = float(rng.uniform(0.0015, 0.010))

        atm_id = "ATM%04d" % (i + 1)
        atm_rows.append((
            atm_id, "%s %s %02d" % (str(regions[i]), lt.replace("_", " ").title(), i + 1),
            lt, str(regions[i]), cap, cap * elig_pct, cap * low_pct,
            fault_prop[i], base_amt[i],
        ))
        for c in range(n_cart):
            cart_rows.append((atm_id, c, int(denoms[i, c]), note_cap))

    conn.executemany(
        "INSERT INTO atms(atm_id,name,location_type,region,capacity,eligibility_level,"
        "low_cash_level,fault_propensity,base_daily_amount) VALUES (?,?,?,?,?,?,?,?,?)",
        atm_rows,
    )
    conn.executemany(
        "INSERT INTO cartridges(atm_id,cartridge_idx,denomination,note_capacity) "
        "VALUES (?,?,?,?)",
        cart_rows,
    )
    conn.commit()

    return build_fleet_arrays(
        atm_ids=[r[0] for r in atm_rows],
        loc_types=[str(x) for x in loc_types],
        denoms=denoms,
        capacity=capacity,
        base_amt=base_amt,
        fault_prop=fault_prop,
        note_cap=note_cap,
    )


def build_fleet_arrays(atm_ids, loc_types, denoms, capacity, base_amt, fault_prop,
                       note_cap) -> dict:
    """Assemble the numpy views used by generation and simulation."""
    n, n_cart = denoms.shape

    # Share of a denomination's notes carried by each cartridge holding it.
    cart_share = np.zeros((n, n_cart))
    for i in range(n):
        for c in range(n_cart):
            same = int(np.sum(denoms[i, :] == denoms[i, c]))
            cart_share[i, c] = 1.0 / same

    # Value share per denomination, restricted to stocked denominations.
    denom_value_share = np.zeros((n, len(DENOMS)))
    for i in range(n):
        pref = LOC_DENOM_VALUE_SHARE[loc_types[i]]
        present = set(int(d) for d in denoms[i, :])
        w = np.array([pref[d] if d in present else 0.0 for d in DENOMS])
        denom_value_share[i, :] = w / w.sum()

    hour_profile = np.zeros((n, 24))
    for i in range(n):
        hour_profile[i, :] = _norm(LOC_HOUR_PROFILE[loc_types[i]])

    return {
        "atm_ids": list(atm_ids),
        "loc_types": list(loc_types),
        "denoms": denoms,
        "capacity": capacity,
        "base_amt": base_amt,
        "fault_prop": fault_prop,
        "cart_share": cart_share,
        "denom_value_share": denom_value_share,
        "hour_profile": hour_profile,
        "note_cap": int(note_cap),
        "n": int(n),
        "n_cart": int(n_cart),
    }


def load_fleet(conn) -> dict:
    """Rebuild fleet arrays from the database."""
    atms = db.query(conn, "SELECT * FROM atms ORDER BY atm_id")
    carts = db.query(conn, "SELECT * FROM cartridges ORDER BY atm_id, cartridge_idx")
    ids = [a["atm_id"] for a in atms]
    idx = {a: i for i, a in enumerate(ids)}
    n_cart = max(c["cartridge_idx"] for c in carts) + 1
    denoms = np.zeros((len(ids), n_cart), dtype=int)
    note_cap = carts[0]["note_capacity"]
    for c in carts:
        denoms[idx[c["atm_id"]], c["cartridge_idx"]] = c["denomination"]
    return build_fleet_arrays(
        atm_ids=ids,
        loc_types=[a["location_type"] for a in atms],
        denoms=denoms,
        capacity=np.array([a["capacity"] for a in atms], dtype=float),
        base_amt=np.array([a["base_daily_amount"] for a in atms], dtype=float),
        fault_prop=np.array([a["fault_propensity"] for a in atms], dtype=float),
        note_cap=note_cap,
    )


def daily_demand_matrix(cfg, rng, fleet, dates) -> np.ndarray:
    """Latent daily demand amount per ATM per date (Req 3)."""
    n = fleet["n"]
    nd = len(dates)
    cal = np.zeros(nd)
    hol = np.zeros(nd, dtype=int)
    for j, day in enumerate(dates):
        cal[j], hol[j] = _calendar_factor(day)

    noise = rng.lognormal(mean=0.0, sigma=0.18, size=(n, nd))
    shock = np.ones((n, nd))
    shock_mask = rng.random((n, nd)) < 0.015
    shock[shock_mask] = rng.uniform(1.7, 2.6, size=int(shock_mask.sum()))

    demand = fleet["base_amt"][:, None] * cal[None, :] * noise * shock
    return np.maximum(demand, 0.0), hol


def notes_per_cartridge_from_amount(fleet, amount_vec) -> np.ndarray:
    """Split a per-ATM demand amount into per-cartridge note counts."""
    n, n_cart = fleet["n"], fleet["n_cart"]
    value_by_denom = amount_vec[:, None] * fleet["denom_value_share"]  # (n, 3)
    notes_by_denom = value_by_denom / np.array(DENOMS, dtype=float)[None, :]
    out = np.zeros((n, n_cart))
    for k, d in enumerate(DENOMS):
        mask = (fleet["denoms"] == d)
        out += mask * (notes_by_denom[:, k][:, None] * fleet["cart_share"])
    return out


def generate_history(conn, cfg, rng, fleet) -> dict:
    """Generate demand history, hourly telemetry, faults, and current state.

    Returns realised arrays reused by the simulator so that all policies face
    identical demand and fault draws (Req 12.10).
    """
    n, n_cart = fleet["n"], fleet["n_cart"]
    note_cap = fleet["note_cap"]
    hist_days = int(cfg["history_days"])
    tel_days = int(cfg["telemetry_days"])
    sim_days = int(cfg["sim_days"])
    low_pct = float(cfg["low_cash_threshold_pct"])
    max_gap = int(cfg["max_refill_gap_days"])

    today = dt.date.today()
    start = today - dt.timedelta(days=hist_days)
    # Extra sim_days of latent demand so the simulator has future draws ready.
    dates = [start + dt.timedelta(days=k) for k in range(hist_days + sim_days + 1)]
    latent, hol = daily_demand_matrix(cfg, rng, fleet, dates)

    cap = fleet["capacity"]
    low_level = cap * low_pct

    # Cartridge note capacity per ATM.
    notes = np.zeros((n, n_cart))
    for i in range(n):
        fill = rng.uniform(0.35, 1.0)
        notes[i, :] = note_cap * fill

    tel_start_idx = hist_days - tel_days
    demand_rows, tel_rows, fault_rows = [], [], []
    unmet_by_day = np.zeros((n, len(dates)))
    served_by_day = np.zeros((n, len(dates)))

    last_refill = np.array([-int(rng.integers(0, max_gap)) for _ in range(n)])
    op_state = ["ok"] * n
    fault_until = np.zeros(n, dtype=int)
    fault_disables = np.zeros(n, dtype=bool)

    for j in range(hist_days + 1):
        day = dates[j]
        # Fault onsets for the day (Req 4).
        onsets = rng.random(n) < fleet["fault_prop"]
        for i in np.nonzero(onsets)[0]:
            cat, dis = FAULT_CATEGORIES[int(rng.integers(len(FAULT_CATEGORIES)))]
            dur = int(rng.integers(2, 19))
            sh = int(rng.integers(0, 24))
            st = dt.datetime.combine(day, dt.time(hour=sh))
            det = st + dt.timedelta(hours=int(rng.integers(0, 3)))
            en = st + dt.timedelta(hours=dur)
            if j >= tel_start_idx:
                fault_rows.append((fleet["atm_ids"][i], cat, int(dis),
                                   st.isoformat(sep=" "), det.isoformat(sep=" "),
                                   en.isoformat(sep=" ")))
            if dis:
                fault_disables[i] = True
                fault_until[i] = dur
                op_state[i] = "out_of_service"
            else:
                op_state[i] = "degraded"

        want_notes = notes_per_cartridge_from_amount(fleet, latent[:, j])

        for h in range(24):
            frac = fleet["hour_profile"][:, h]
            hour_want = want_notes * frac[:, None]
            blocked = fault_disables & (fault_until > 0)
            hour_want[blocked, :] = 0.0

            served = np.minimum(notes, hour_want)
            notes -= served

            served_amt = (served * fleet["denoms"]).sum(axis=1)
            want_amt = (hour_want * fleet["denoms"]).sum(axis=1)
            served_by_day[:, j] += served_amt
            unmet_by_day[:, j] += np.maximum(want_amt - served_amt, 0.0)
            # Demand suppressed by an outage still counts as unmet (Req 4.4).
            blocked_amt = (want_notes * frac[:, None] * fleet["denoms"]).sum(axis=1)
            unmet_by_day[blocked, j] += blocked_amt[blocked]

            if j >= tel_start_idx:
                total = (notes * fleet["denoms"]).sum(axis=1)
                pct = total / cap
                win = "P0" if h < 8 else ("P1" if h < 12 else "P2")
                ts = dt.datetime.combine(day, dt.time(hour=h)).isoformat(sep=" ")
                for i in range(n):
                    band = ("below_threshold" if pct[i] < low_pct
                            else ("in_band" if pct[i] < cfg["refill_eligibility_pct"]
                                  else "above_eligibility"))
                    tel_rows.append((
                        fleet["atm_ids"][i], ts, float(total[i]), float(pct[i]),
                        band, win, op_state[i],
                        db.json_dumps([int(round(x)) for x in notes[i, :]]),
                    ))

        fault_until = np.maximum(fault_until - 24, 0)
        for i in range(n):
            if fault_until[i] == 0:
                fault_disables[i] = False
                op_state[i] = "ok"

        # Reactive warm-up policy: refill when below the floor or at the gap limit.
        total = (notes * fleet["denoms"]).sum(axis=1)
        due = (total < low_level) | ((j - last_refill) >= max_gap)
        for i in np.nonzero(due)[0]:
            notes[i, :] = note_cap
            last_refill[i] = j

        served_total = served_by_day[:, j]
        npc = notes_per_cartridge_from_amount(fleet, served_total)
        for i in range(n):
            nb = {10: 0, 50: 0, 100: 0}
            for c in range(n_cart):
                nb[int(fleet["denoms"][i, c])] += int(round(npc[i, c]))
            p0 = float(fleet["hour_profile"][i, 0:8].sum())
            p1 = float(fleet["hour_profile"][i, 8:12].sum())
            p2 = float(fleet["hour_profile"][i, 12:24].sum())
            demand_rows.append((
                fleet["atm_ids"][i], day.isoformat(), float(served_total[i]),
                nb[10], nb[50], nb[100], p0, p1, p2,
                float(unmet_by_day[i, j]), int(hol[j]),
            ))

    conn.executemany(
        "INSERT INTO demand_daily(atm_id,d,amount,notes_10,notes_50,notes_100,"
        "p0_share,p1_share,p2_share,unmet_amount,is_holiday) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", demand_rows)
    conn.executemany(
        "INSERT INTO telemetry(atm_id,ts,total_balance,pct_of_capacity,band_state,"
        "priority_window,op_state,cartridge_notes) VALUES (?,?,?,?,?,?,?,?)", tel_rows)
    conn.executemany(
        "INSERT INTO faults(atm_id,category,disables_dispensing,start_ts,detected_ts,"
        "end_ts) VALUES (?,?,?,?,?,?)", fault_rows)

    total = (notes * fleet["denoms"]).sum(axis=1)
    state_rows = []
    for i in range(n):
        lr = dates[max(last_refill[i], 0)].isoformat()
        state_rows.append((
            fleet["atm_ids"][i], today.isoformat(),
            db.json_dumps([int(round(x)) for x in notes[i, :]]),
            float(total[i]), float(total[i] / cap[i]), lr,
        ))
    conn.executemany(
        "INSERT INTO atm_state(atm_id,as_of,cartridge_notes,total_balance,"
        "pct_of_capacity,last_refill_date) VALUES (?,?,?,?,?,?)", state_rows)
    conn.commit()

    return {
        "dates": dates,
        "latent": latent,
        "hist_days": hist_days,
        "today": today,
        "start_notes": notes.copy(),
        "last_refill_idx": last_refill.copy(),
    }


def run(conn, cfg, seed: int | None = None) -> dict:
    rng = np.random.default_rng(int(cfg["seed"]) if seed is None else int(seed))
    fleet = generate_fleet(conn, cfg, rng)
    hist = generate_history(conn, cfg, rng, fleet)
    return {"fleet": fleet, "history": hist}
