"""End-to-end pipeline: build the database, train the model, plan, simulate.

    python run_pipeline.py                 # full run
    python run_pipeline.py --skip-sim      # data + model + plan only (faster)
    python run_pipeline.py --seed 7        # different random draw
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

from src import db, forecast, generate, scheduler, simulate
from src.config import load_config


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--skip-sim", action="store_true")
    args = ap.parse_args()

    overrides = {"seed": args.seed} if args.seed is not None else None
    cfg = load_config(args.config, overrides)
    t0 = time.time()

    print("ATM cash replenishment planner - pipeline")
    print("  config: %d ATMs, %d days history, %d day horizon, cap %d/day "
          "(%d planned + %d ad hoc reserve)"
          % (cfg["n_atms"], cfg["history_days"], cfg["planning_horizon_days"],
             cfg["daily_trip_cap"], cfg.effective_daily_cap,
             cfg["adhoc_reserve_trips"]))

    conn = db.connect(cfg.db_path())
    print("[1/6] initialising schema at %s" % cfg.db_path())
    db.init_schema(conn)

    print("[2/6] generating fleet, demand, telemetry and faults ...")
    gen = generate.run(conn, cfg)
    fleet, hist = gen["fleet"], gen["history"]
    n_tel = db.scalar(conn, "SELECT COUNT(*) FROM telemetry", default=0)
    n_dem = db.scalar(conn, "SELECT COUNT(*) FROM demand_daily", default=0)
    n_fault = db.scalar(conn, "SELECT COUNT(*) FROM faults", default=0)
    print("      %d ATMs, %d demand rows, %d telemetry rows, %d faults"
          % (fleet["n"], n_dem, n_tel, n_fault))

    print("[3/6] training quantile demand model ...")
    bundle = forecast.train(conn, cfg)

    print("[4/6] planning ...")
    ids, dates, A, _ = forecast.load_history(conn)
    as_of_j = A.shape[1] - 1
    as_of = dates[-1]
    state = db.query(conn, "SELECT * FROM atm_state ORDER BY atm_id")
    import numpy as np
    notes = np.array([db.json_loads(r["cartridge_notes"]) for r in state], dtype=float)
    last_refill = [dt.date.fromisoformat(r["last_refill_date"]) for r in state]

    forecast.store_forecasts(conn, bundle, fleet, A, as_of_j, as_of,
                             int(cfg["planning_horizon_days"]))
    run_id = scheduler.plan_now(conn, cfg, fleet, bundle, A, as_of_j, as_of,
                                notes, last_refill, seed=int(cfg["seed"]))
    first = db.one(conn, "SELECT MIN(plan_date) d FROM schedule WHERE run_id=?", (run_id,))
    day1 = db.scalar(conn, "SELECT COUNT(*) FROM schedule WHERE run_id=? AND plan_date=?",
                     (run_id, first["d"]), default=0)
    total = db.scalar(conn, "SELECT COUNT(*) FROM schedule WHERE run_id=?", (run_id,),
                      default=0)
    print("      run %d: %d visits over %d days, %d on the first operating day (%s)"
          % (run_id, total, cfg["planning_horizon_days"], day1, first["d"]))

    print("[5/6] validating schedule ...")
    checks = simulate.validate_schedule(conn, cfg, run_id)
    for c in checks:
        print("      %-38s %s (observed %.0f, limit %.0f)"
              % (c["check_name"], "PASS" if c["passed"] else "FAIL",
                 c["observed"], c["limit_val"]))

    if args.skip_sim:
        print("[6/6] simulation skipped")
    else:
        print("[6/6] simulating policies over %d days ..." % cfg["sim_days"])
        res = simulate.run_all(conn, cfg, fleet, bundle, hist)
        print()
        hdr = ("policy", "trips", "planned", "adhoc", "adhoc%", "stockouts",
               "breach h", "fill%", "in band%")
        print("  %-16s %7s %8s %7s %7s %10s %9s %7s %9s" % hdr)
        for p in simulate.POLICIES:
            m = res[p]["metrics"]
            print("  %-16s %7.0f %8.0f %7.0f %6.1f%% %10.0f %9.0f %6.1f%% %8.1f%%"
                  % (p, m["total_trips"], m["planned_trips"], m["adhoc_trips"],
                     m["adhoc_rate_pct"], m["stockout_events"],
                     m["low_cash_breach_hours"], m["mean_fill_at_refill_pct"],
                     m["refills_inside_band_pct"]))

    conn.close()
    print("\ndone in %.1fs   ->  start the dashboard with:  python app.py"
          % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
