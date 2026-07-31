"""Flask backend for the operator dashboard (Requirements 14, 15).

Read-only over the SQLite database produced by run_pipeline.py, plus a replan
endpoint that re-runs the planner with adjusted parameters.

Note: this binds to localhost and has no authentication. It is a local
prototype dashboard; do not expose it on a network without adding auth.
"""
from __future__ import annotations

import datetime as dt
import os

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

from src import db, forecast, generate, scheduler, simulate
from src.config import load_config, REPO_ROOT

WEB_DIR = os.path.join(REPO_ROOT, "web")
app = Flask(__name__, static_folder=None)
CFG = load_config()


def conn():
    return db.connect(CFG.db_path())


def latest_run(c) -> int | None:
    return db.scalar(c, "SELECT MAX(run_id) FROM plan_runs")


# --------------------------------------------------------------- static assets
@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


# ------------------------------------------------------------------------ API
@app.get("/api/summary")
def api_summary():
    c = conn()
    run_id = latest_run(c)
    if run_id is None:
        return jsonify({"ready": False,
                        "message": "No plan found. Run: python run_pipeline.py"})
    run = db.one(c, "SELECT * FROM plan_runs WHERE run_id=?", (run_id,))
    dates = db.query(
        c, "SELECT plan_date, COUNT(*) c FROM schedule WHERE run_id=? "
           "GROUP BY plan_date ORDER BY plan_date", (run_id,))
    fleet_n = db.scalar(c, "SELECT COUNT(*) FROM atms", default=0)
    bands = db.query(
        c, "SELECT band_state, COUNT(*) c FROM (SELECT CASE "
           "WHEN pct_of_capacity < ? THEN 'below_threshold' "
           "WHEN pct_of_capacity < ? THEN 'in_band' ELSE 'above_eligibility' END "
           "AS band_state FROM atm_state) GROUP BY band_state",
        (CFG["low_cash_threshold_pct"], CFG["refill_eligibility_pct"]))
    acc = db.query(c, "SELECT model, metric, value FROM forecast_accuracy")
    metrics = {p: db.get_metrics(c, p) for p in simulate.POLICIES}
    checks = db.query(
        c, "SELECT check_name, passed, observed, limit_val, detail "
           "FROM validation_results WHERE run_id=?", (run_id,))
    recs = db.scalar(c, "SELECT COUNT(*) FROM denom_recommendations", default=0)
    c.close()
    return jsonify({
        "ready": True,
        "run_id": run_id,
        "as_of": run["as_of"],
        "created_at": run["created_at"],
        "fleet_size": fleet_n,
        "daily_trip_cap": CFG["daily_trip_cap"],
        "effective_cap": CFG.effective_daily_cap,
        "eligibility_pct": CFG["refill_eligibility_pct"],
        "threshold_pct": CFG["low_cash_threshold_pct"],
        "horizon": CFG["planning_horizon_days"],
        "per_day": dates,
        "band_counts": {b["band_state"]: b["c"] for b in bands},
        "forecast_accuracy": acc,
        "policy_metrics": metrics,
        "validation": checks,
        "denom_recommendations": recs,
    })


@app.get("/api/schedule")
def api_schedule():
    c = conn()
    run_id = latest_run(c)
    if run_id is None:
        return jsonify({"rows": []})
    date = request.args.get("date")
    if not date:
        date = db.scalar(c, "SELECT MIN(plan_date) FROM schedule WHERE run_id=?",
                         (run_id,))
    rows = db.query(
        c,
        "SELECT s.schedule_id, s.atm_id, s.plan_date, s.priority_window, "
        "s.compliance_driven, s.load_total, s.pct_at_visit, s.reason, s.urgency, "
        "a.name, a.location_type, a.region, a.capacity, a.low_cash_level, "
        "st.total_balance, st.pct_of_capacity, st.last_refill_date, "
        "w.opens_date, w.closes_date, w.effective_latest, w.driver "
        "FROM schedule s JOIN atms a ON a.atm_id=s.atm_id "
        "JOIN atm_state st ON st.atm_id=s.atm_id "
        "LEFT JOIN refill_windows w ON w.atm_id=s.atm_id AND w.as_of="
        "(SELECT as_of FROM plan_runs WHERE run_id=?) "
        "WHERE s.run_id=? AND s.plan_date=? "
        "ORDER BY CASE s.priority_window WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, "
        "s.urgency", (run_id, run_id, date))
    for r in rows:
        r["loads"] = db.query(
            c, "SELECT cartridge_idx, denomination, load_notes, load_amount "
               "FROM schedule_loads WHERE schedule_id=? ORDER BY cartridge_idx",
            (r["schedule_id"],))
    counts = {}
    for r in rows:
        counts[r["priority_window"]] = counts.get(r["priority_window"], 0) + 1
    target = scheduler.largest_remainder(len(rows), CFG["priority_mix"]) if rows else {}
    all_dates = [x["plan_date"] for x in db.query(
        c, "SELECT DISTINCT plan_date FROM schedule WHERE run_id=? ORDER BY plan_date",
        (run_id,))]
    c.close()
    return jsonify({"date": date, "dates": all_dates, "rows": rows,
                    "band_counts": counts, "band_target": target})


@app.get("/api/fleet")
def api_fleet():
    c = conn()
    run_id = latest_run(c)
    as_of = db.scalar(c, "SELECT as_of FROM plan_runs WHERE run_id=?", (run_id,)) \
        if run_id else None
    rows = db.query(
        c,
        "SELECT a.atm_id, a.name, a.location_type, a.region, a.capacity, "
        "st.total_balance, st.pct_of_capacity, st.last_refill_date, "
        "w.opens_date, w.closes_date, w.effective_latest, w.driver, w.slack_days, "
        "w.compliance_low_value, s.plan_date, s.priority_window "
        "FROM atms a JOIN atm_state st ON st.atm_id=a.atm_id "
        "LEFT JOIN refill_windows w ON w.atm_id=a.atm_id AND w.as_of=? "
        "LEFT JOIN schedule s ON s.atm_id=a.atm_id AND s.run_id=? "
        "ORDER BY w.slack_days, a.atm_id", (as_of, run_id))
    c.close()
    return jsonify({"rows": rows,
                    "eligibility_pct": CFG["refill_eligibility_pct"],
                    "threshold_pct": CFG["low_cash_threshold_pct"]})


@app.get("/api/atm/<atm_id>")
def api_atm(atm_id):
    c = conn()
    run_id = latest_run(c)
    as_of = db.scalar(c, "SELECT as_of FROM plan_runs WHERE run_id=?", (run_id,)) \
        if run_id else None
    atm = db.one(c, "SELECT * FROM atms WHERE atm_id=?", (atm_id,))
    if atm is None:
        c.close()
        return jsonify({"error": "unknown atm"}), 404
    state = db.one(c, "SELECT * FROM atm_state WHERE atm_id=?", (atm_id,))
    carts = db.query(c, "SELECT * FROM cartridges WHERE atm_id=? ORDER BY cartridge_idx",
                     (atm_id,))
    window = db.one(c, "SELECT * FROM refill_windows WHERE atm_id=? AND as_of=?",
                    (atm_id, as_of))
    sched = db.one(c, "SELECT * FROM schedule WHERE run_id=? AND atm_id=?",
                   (run_id, atm_id))
    tel = db.query(
        c, "SELECT ts, total_balance, pct_of_capacity, op_state FROM telemetry "
           "WHERE atm_id=? ORDER BY ts", (atm_id,))
    fc = db.query(
        c, "SELECT target_date, horizon, q50, q90, q95 FROM forecasts "
           "WHERE atm_id=? AND as_of=? ORDER BY horizon", (atm_id, as_of))
    hist = db.query(
        c, "SELECT d, amount, unmet_amount FROM demand_daily WHERE atm_id=? "
           "ORDER BY d DESC LIMIT 90", (atm_id,))
    faults = db.query(
        c, "SELECT category, disables_dispensing, start_ts, end_ts FROM faults "
           "WHERE atm_id=? ORDER BY start_ts DESC LIMIT 20", (atm_id,))
    rec = db.one(c, "SELECT * FROM denom_recommendations WHERE atm_id=?", (atm_id,))

    # Projected balance trajectory against the 55% and 25% levels (Req 14.7).
    traj, bal = [], state["total_balance"]
    for f in fc:
        bal = max(bal - f["q90"], 0.0)
        traj.append({"date": f["target_date"], "balance": bal,
                     "pct": bal / atm["capacity"]})
    c.close()
    return jsonify({
        "atm": atm, "state": state, "cartridges": carts, "window": window,
        "schedule": sched, "telemetry": tel, "forecast": fc,
        "history": list(reversed(hist)), "faults": faults,
        "trajectory": traj, "denom_recommendation": rec,
        "eligibility_pct": CFG["refill_eligibility_pct"],
        "threshold_pct": CFG["low_cash_threshold_pct"],
    })


@app.get("/api/benchmark")
def api_benchmark():
    c = conn()
    out = {p: db.get_metrics(c, p) for p in simulate.POLICIES}
    trend = {}
    for p in simulate.POLICIES:
        trend[p] = db.query(
            c, "SELECT d, COUNT(*) c, SUM(CASE WHEN trip_type='adhoc' THEN 1 ELSE 0 END) "
               "adhoc FROM sim_trips WHERE policy=? GROUP BY d ORDER BY d", (p,))
    c.close()
    return jsonify({"metrics": out, "labels": simulate.POLICY_LABELS, "trend": trend})


@app.get("/api/adhoc")
def api_adhoc():
    """Ad hoc trips raised under the planner policy during simulation."""
    c = conn()
    rows = db.query(
        c, "SELECT t.atm_id, t.d, t.trigger, t.cash_loaded, t.pct_before, a.name, "
           "a.location_type FROM sim_trips t JOIN atms a ON a.atm_id=t.atm_id "
           "WHERE t.policy='ai_planner' AND t.trip_type='adhoc' "
           "ORDER BY t.d DESC, t.atm_id LIMIT 200")
    by_trigger = db.query(
        c, "SELECT trigger, COUNT(*) c FROM sim_trips WHERE policy='ai_planner' "
           "AND trip_type='adhoc' GROUP BY trigger")
    c.close()
    return jsonify({"rows": rows, "by_trigger": by_trigger})


@app.get("/api/recommendations")
def api_recommendations():
    c = conn()
    rows = db.query(
        c, "SELECT r.*, a.name, a.location_type FROM denom_recommendations r "
           "JOIN atms a ON a.atm_id=r.atm_id ORDER BY "
           "(r.projected_interval_days - r.current_interval_days) DESC")
    c.close()
    return jsonify({"rows": rows})


@app.get("/api/explain/<atm_id>")
def api_explain(atm_id):
    """Plain-language reason, including why a deferral is safe (Req 15.2)."""
    c = conn()
    run_id = latest_run(c)
    as_of = db.scalar(c, "SELECT as_of FROM plan_runs WHERE run_id=?", (run_id,))
    sched = db.one(c, "SELECT * FROM schedule WHERE run_id=? AND atm_id=?",
                   (run_id, atm_id))
    w = db.one(c, "SELECT * FROM refill_windows WHERE atm_id=? AND as_of=?",
               (atm_id, as_of))
    st = db.one(c, "SELECT * FROM atm_state WHERE atm_id=?", (atm_id,))
    c.close()
    if w is None or st is None:
        return jsonify({"error": "unknown atm"}), 404
    pct = 100.0 * st["pct_of_capacity"]
    if sched:
        text = sched["reason"]
        text += (" Scheduled %s in %s. Currently at %.0f%% of capacity, last refilled %s."
                 % (sched["plan_date"], sched["priority_window"], pct,
                    st["last_refill_date"]))
    else:
        text = ("Not scheduled in this horizon. At %.0f%% of capacity the forecast "
                "keeps it above the 25%% floor until %s, and the refill-interval "
                "deadline is %s, so deferral is safe."
                % (pct, w["closes_date"] or "beyond the horizon",
                   w["regulatory_deadline"]))
    return jsonify({"atm_id": atm_id, "explanation": text, "window": w,
                    "scheduled": bool(sched)})


@app.post("/api/ask")
def api_ask():
    """Deterministic query handler grounded in the stored plan (Req 15.5).

    Intentionally rule-based rather than an LLM so every answer is traceable to
    a database row. See README for where a hosted model would slot in.
    """
    q = (request.json or {}).get("question", "").lower().strip()
    c = conn()
    run_id = latest_run(c)
    if run_id is None:
        c.close()
        return jsonify({"answer": "No plan has been generated yet."})
    first = db.scalar(c, "SELECT MIN(plan_date) FROM schedule WHERE run_id=?", (run_id,))

    import re
    m = re.search(r"atm\s*0*(\d+)", q)
    if m:
        aid = "ATM%04d" % int(m.group(1))
        c.close()
        return api_explain(aid)

    if "how many" in q and ("trip" in q or "visit" in q):
        n = db.scalar(c, "SELECT COUNT(*) FROM schedule WHERE run_id=? AND plan_date=?",
                      (run_id, first), default=0)
        bands = db.query(c, "SELECT priority_window b, COUNT(*) c FROM schedule "
                            "WHERE run_id=? AND plan_date=? GROUP BY b", (run_id, first))
        c.close()
        detail = ", ".join("%s %d" % (b["b"], b["c"]) for b in bands)
        return jsonify({"answer": "%d visits planned for %s (%s). The cap is %d/day."
                                  % (n, first, detail, CFG["daily_trip_cap"])})

    if "risk" in q or "urgent" in q or "low" in q:
        rows = db.query(
            c, "SELECT atm_id, pct_now, closes_date, slack_days FROM refill_windows "
               "WHERE as_of=(SELECT as_of FROM plan_runs WHERE run_id=?) "
               "ORDER BY slack_days LIMIT 10", (run_id,))
        c.close()
        return jsonify({"answer": "Ten tightest ATMs by slack:",
                        "rows": rows})

    if "compliance" in q or "14 day" in q or "15 day" in q:
        n = db.scalar(c, "SELECT COUNT(*) FROM schedule WHERE run_id=? AND "
                         "compliance_driven=1", (run_id,), default=0)
        c.close()
        return jsonify({"answer": "%d visits in this horizon exist only to satisfy the "
                                  "refill-interval rule, not because cash is needed." % n})

    if "adhoc" in q or "ad hoc" in q or "unplanned" in q:
        m2 = db.get_metrics(c, "ai_planner")
        c.close()
        return jsonify({"answer": "Simulated ad hoc trips under the planner: %.0f of "
                                  "%.0f total (%.1f%%)."
                                  % (m2.get("adhoc_trips", 0), m2.get("total_trips", 0),
                                     m2.get("adhoc_rate_pct", 0))})
    c.close()
    return jsonify({"answer": "Try: 'how many trips today', 'which ATMs are at risk', "
                              "'why ATM 42', 'how many compliance trips', "
                              "'how many ad hoc trips'."})


@app.post("/api/replan")
def api_replan():
    """Regenerate the plan with adjusted parameters (Req 14.6)."""
    body = request.json or {}
    allowed = ("safety_quantile", "refill_eligibility_pct", "low_cash_threshold_pct",
               "planning_horizon_days", "adhoc_reserve_trips", "daily_trip_cap",
               "objective_weights")
    overrides = {k: body[k] for k in allowed if k in body}
    try:
        cfg = load_config(None, overrides)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    c = conn()
    fleet = generate.load_fleet(c)
    try:
        bundle = forecast.train(c, cfg, verbose=False)
    except Exception as exc:
        c.close()
        return jsonify({"error": "model training failed: %s" % exc}), 500

    ids, dates, A, _ = forecast.load_history(c)
    as_of_j, as_of = A.shape[1] - 1, dates[-1]
    state = db.query(c, "SELECT * FROM atm_state ORDER BY atm_id")
    notes = np.array([db.json_loads(r["cartridge_notes"]) for r in state], dtype=float)
    last_refill = [dt.date.fromisoformat(r["last_refill_date"]) for r in state]

    forecast.store_forecasts(c, bundle, fleet, A, as_of_j, as_of,
                             int(cfg["planning_horizon_days"]))
    run_id = scheduler.plan_now(c, cfg, fleet, bundle, A, as_of_j, as_of, notes,
                                last_refill, seed=int(cfg["seed"]))
    checks = simulate.validate_schedule(c, cfg, run_id)
    per_day = db.query(c, "SELECT plan_date, COUNT(*) c FROM schedule WHERE run_id=? "
                          "GROUP BY plan_date ORDER BY plan_date", (run_id,))
    total = db.scalar(c, "SELECT COUNT(*) FROM schedule WHERE run_id=?", (run_id,),
                      default=0)
    compliance = db.scalar(c, "SELECT COUNT(*) FROM schedule WHERE run_id=? AND "
                              "compliance_driven=1", (run_id,), default=0)
    avg_fill = db.scalar(c, "SELECT AVG(pct_at_visit) FROM schedule WHERE run_id=?",
                         (run_id,), default=0)
    c.close()
    return jsonify({"run_id": run_id, "total_visits": total, "per_day": per_day,
                    "compliance_driven": compliance,
                    "mean_fill_at_visit_pct": 100.0 * (avg_fill or 0),
                    "validation": checks})


if __name__ == "__main__":
    if not os.path.exists(CFG.db_path()):
        print("No database at %s\nRun:  python run_pipeline.py" % CFG.db_path())
    print("Dashboard: http://127.0.0.1:5000  (local only, no authentication)")
    app.run(host="127.0.0.1", port=5000, debug=False)
