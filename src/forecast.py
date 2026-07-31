"""Quantile demand forecasting (Requirement 5).

A single pooled gradient-boosting model is trained across the whole fleet with
per-ATM features, which works better than 350 independent series models at this
data volume. Three quantiles are fitted (50th, 90th, 95th); the planner consumes
the upper quantile so that refill timing carries a safety margin.

Forecasts are "direct multi-horizon": the horizon is a model feature, so no
recursive feeding of predictions is needed.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from . import db
from .generate import DENOMS, HOLIDAYS, CNY_DATES, LOCATION_TYPES

WARMUP = 28
TRAIN_HORIZONS = (1, 2, 3, 5, 7, 10, 14, 18, 21)
AS_OF_STRIDE = 3

FEATURES = [
    "h", "dow", "dom", "month", "is_holiday", "is_payday", "cny_window",
    "ratio_m7_m28", "cv28", "naive_ratio", "last_ratio", "loc_code",
    "log_capacity", "utilisation",
]


def _payday_flag(day: dt.date) -> int:
    dom = day.day
    nxt = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    dim = (nxt - dt.timedelta(days=1)).day
    if dom >= dim - 1 or dom <= 2:
        return 2
    if 14 <= dom <= 16:
        return 1
    return 0


def _cny_window(day: dt.date) -> int:
    for c in CNY_DATES:
        delta = (dt.date.fromisoformat(c) - day).days
        if 0 <= delta <= 7:
            return 8 - delta
    return 0


def calendar_table(dates) -> dict:
    return {
        "dow": np.array([d.weekday() for d in dates]),
        "dom": np.array([d.day for d in dates]),
        "month": np.array([d.month for d in dates]),
        "is_holiday": np.array([1 if d.isoformat() in HOLIDAYS else 0 for d in dates]),
        "is_payday": np.array([_payday_flag(d) for d in dates]),
        "cny_window": np.array([_cny_window(d) for d in dates]),
    }


def load_history(conn) -> tuple[list[str], list[dt.date], np.ndarray, np.ndarray]:
    """Return (atm_ids, dates, amounts, unmet) matrices from demand_daily."""
    ids = [r["atm_id"] for r in db.query(conn, "SELECT atm_id FROM atms ORDER BY atm_id")]
    ds = [r["d"] for r in db.query(conn, "SELECT DISTINCT d FROM demand_daily ORDER BY d")]
    idx_i = {a: i for i, a in enumerate(ids)}
    idx_j = {d: j for j, d in enumerate(ds)}
    A = np.zeros((len(ids), len(ds)))
    U = np.zeros((len(ids), len(ds)))
    for r in conn.execute("SELECT atm_id, d, amount, unmet_amount FROM demand_daily"):
        A[idx_i[r["atm_id"]], idx_j[r["d"]]] = r["amount"]
        U[idx_i[r["atm_id"]], idx_j[r["d"]]] = r["unmet_amount"]
    return ids, [dt.date.fromisoformat(d) for d in ds], A, U


def rolling_stats(A: np.ndarray) -> dict:
    """Trailing mean/std computed with cumulative sums (inclusive of column j)."""
    n, nd = A.shape
    csum = np.cumsum(A, axis=1)
    csq = np.cumsum(A * A, axis=1)

    def window_sum(c, w):
        out = np.empty_like(c)
        out[:, :w] = c[:, :w]
        out[:, w:] = c[:, w:] - c[:, :-w]
        return out

    s7 = window_sum(csum, 7)
    s28 = window_sum(csum, 28)
    q28 = window_sum(csq, 28)
    cnt = np.minimum(np.arange(1, nd + 1), 28)[None, :]
    cnt7 = np.minimum(np.arange(1, nd + 1), 7)[None, :]
    m28 = s28 / cnt
    m7 = s7 / cnt7
    var = np.maximum(q28 / cnt - m28 ** 2, 0.0)
    return {"m7": m7, "m28": m28, "sd28": np.sqrt(var)}


def _feature_block(fleet_static, stats, cal, A, i_idx, j_idx, h_arr):
    """Assemble the feature matrix for aligned (atm, as_of, horizon) triples."""
    m28 = stats["m28"][i_idx, j_idx]
    m28safe = np.where(m28 <= 1e-6, 1.0, m28)
    t_idx = j_idx + h_arr
    ref_idx = t_idx - 7 * np.ceil(h_arr / 7.0).astype(int)
    ref_idx = np.clip(ref_idx, 0, A.shape[1] - 1)

    X = np.column_stack([
        h_arr,
        cal["dow"][t_idx],
        cal["dom"][t_idx],
        cal["month"][t_idx],
        cal["is_holiday"][t_idx],
        cal["is_payday"][t_idx],
        cal["cny_window"][t_idx],
        stats["m7"][i_idx, j_idx] / m28safe,
        stats["sd28"][i_idx, j_idx] / m28safe,
        A[i_idx, ref_idx] / m28safe,
        A[i_idx, j_idx] / m28safe,
        fleet_static["loc_code"][i_idx],
        fleet_static["log_capacity"][i_idx],
        m28 / fleet_static["capacity"][i_idx],
    ])
    return X, m28safe, t_idx, ref_idx


def _static(conn, ids) -> dict:
    rows = db.query(conn, "SELECT atm_id, capacity, location_type FROM atms ORDER BY atm_id")
    code = {lt: k for k, lt in enumerate(LOCATION_TYPES)}
    cap = np.array([r["capacity"] for r in rows], dtype=float)
    return {
        "capacity": cap,
        "log_capacity": np.log(cap),
        "loc_code": np.array([code[r["location_type"]] for r in rows]),
    }


def _pinball(y, pred, q) -> float:
    d = y - pred
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def train(conn, cfg, verbose: bool = True) -> dict:
    ids, dates, A, U = load_history(conn)
    stats = rolling_stats(A)
    cal = calendar_table(dates)
    static = _static(conn, ids)
    n, nd = A.shape
    quantiles = [float(q) for q in cfg["forecast_quantiles"]]
    max_h = max(TRAIN_HORIZONS)

    # Walk-forward split: the final 28 days of as_of dates are held out (Req 5.7).
    last_as_of = nd - 1 - max_h
    test_lo = max(WARMUP, last_as_of - 28)
    train_as_of = list(range(WARMUP, test_lo, AS_OF_STRIDE))
    test_as_of = list(range(test_lo, last_as_of + 1))

    def build(as_of_list):
        ii, jj, hh = [], [], []
        for j in as_of_list:
            for h in TRAIN_HORIZONS:
                if j + h >= nd:
                    continue
                ii.append(np.arange(n))
                jj.append(np.full(n, j))
                hh.append(np.full(n, h))
        if not ii:
            return None
        i_idx = np.concatenate(ii)
        j_idx = np.concatenate(jj)
        h_arr = np.concatenate(hh)
        X, m28, t_idx, ref_idx = _feature_block(static, stats, cal, A, i_idx, j_idx, h_arr)
        y_amt = A[i_idx, t_idx]
        y = y_amt / m28
        # Req 5.6: drop targets whose demand was suppressed by stockout/outage.
        keep = (U[i_idx, t_idx] <= 1e-9) & (m28 > 1e-6)
        return {
            "X": X[keep], "y": y[keep], "y_amt": y_amt[keep], "m28": m28[keep],
            "naive": A[i_idx, ref_idx][keep], "h": h_arr[keep],
        }

    tr = build(train_as_of)
    te = build(test_as_of)
    if tr is None or te is None:
        raise RuntimeError("not enough history to build a train/test split")

    models = {}
    for q in quantiles:
        m = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, max_iter=220, learning_rate=0.08,
            max_depth=7, min_samples_leaf=40, l2_regularization=1.0,
            random_state=int(cfg["seed"]),
        )
        m.fit(tr["X"], tr["y"])
        models[q] = m
        if verbose:
            print("    trained quantile %.2f on %d rows" % (q, len(tr["y"])))

    # ---- evaluation on the held-out period (Req 5.8, 5.9) -------------------
    conn.execute("DELETE FROM forecast_accuracy")
    acc = []
    y_amt = te["y_amt"]
    for q in quantiles:
        pred = np.maximum(models[q].predict(te["X"]), 0.0) * te["m28"]
        acc.append(("ml_pooled_gbm", "pinball_q%02d" % int(q * 100), _pinball(y_amt, pred, q)))
        acc.append(("ml_pooled_gbm", "coverage_q%02d" % int(q * 100),
                    float(np.mean(y_amt <= pred))))
        if abs(q - 0.5) < 1e-9:
            acc.append(("ml_pooled_gbm", "mae", float(np.mean(np.abs(y_amt - pred)))))
            denom = np.where(y_amt <= 1e-6, np.nan, y_amt)
            acc.append(("ml_pooled_gbm", "mape",
                        float(np.nanmean(np.abs((y_amt - pred) / denom)))))

    nv = te["naive"]
    acc.append(("seasonal_naive", "mae", float(np.mean(np.abs(y_amt - nv)))))
    acc.append(("seasonal_naive", "pinball_q50", _pinball(y_amt, nv, 0.5)))
    ml_mae = [v for m_, k, v in acc if m_ == "ml_pooled_gbm" and k == "mae"][0]
    nv_mae = [v for m_, k, v in acc if m_ == "seasonal_naive" and k == "mae"][0]
    acc.append(("comparison", "mae_improvement_pct",
                float(100.0 * (nv_mae - ml_mae) / nv_mae) if nv_mae else 0.0))
    acc.append(("comparison", "beats_seasonal_naive", 1.0 if ml_mae < nv_mae else 0.0))
    acc.append(("ml_pooled_gbm", "test_rows", float(len(y_amt))))
    acc.append(("ml_pooled_gbm", "train_rows", float(len(tr["y"]))))

    conn.executemany(
        "INSERT INTO forecast_accuracy(model,metric,value) VALUES (?,?,?)", acc)
    conn.commit()
    if verbose:
        print("    holdout MAE: model %.0f vs seasonal-naive %.0f (%.1f%% better)"
              % (ml_mae, nv_mae, 100.0 * (nv_mae - ml_mae) / max(nv_mae, 1e-9)))

    return {
        "models": models, "quantiles": quantiles, "static": static,
        "ids": ids, "dates": dates, "cal": cal,
    }


def predict(bundle, A: np.ndarray, as_of_j: int, horizon: int) -> np.ndarray:
    """Forecast amounts for horizons 1..horizon.

    Returns an array shaped (n_atms, horizon, n_quantiles) ordered by the
    quantile list in the bundle.
    """
    stats = rolling_stats(A[:, : as_of_j + 1])
    n = A.shape[0]
    hs = np.arange(1, horizon + 1)
    i_idx = np.tile(np.arange(n), horizon)
    j_idx = np.full(n * horizon, as_of_j)
    h_arr = np.repeat(hs, n)

    # Pad the calendar/amount matrices so target indices beyond as_of resolve.
    need = as_of_j + horizon + 1
    Apad = A
    if A.shape[1] < need:
        Apad = np.pad(A, ((0, 0), (0, need - A.shape[1])), mode="edge")
    cal = bundle["cal"]
    if len(cal["dow"]) < need:
        base = bundle["dates"][0]
        ext = [base + dt.timedelta(days=k) for k in range(need)]
        cal = calendar_table(ext)

    stats_full = {k: np.pad(v, ((0, 0), (0, max(0, need - v.shape[1]))), mode="edge")
                  for k, v in stats.items()}
    X, m28, _, _ = _feature_block(bundle["static"], stats_full, cal, Apad,
                                  i_idx, j_idx, h_arr)

    out = np.zeros((n, horizon, len(bundle["quantiles"])))
    for k, q in enumerate(bundle["quantiles"]):
        p = np.maximum(bundle["models"][q].predict(X), 0.0) * m28
        out[:, :, k] = p.reshape(horizon, n).T
    # Enforce monotone quantiles so q95 >= q90 >= q50.
    out = np.sort(out, axis=2)
    return out


def store_forecasts(conn, bundle, fleet, A, as_of_j, as_of_date, horizon) -> None:
    preds = predict(bundle, A, as_of_j, horizon)
    qs = bundle["quantiles"]
    k50, k90, k95 = qs.index(0.5), qs.index(0.9), qs.index(0.95)
    rows = []
    for i, atm_id in enumerate(bundle["ids"]):
        p0 = float(fleet["hour_profile"][i, 0:8].sum())
        p1 = float(fleet["hour_profile"][i, 8:12].sum())
        p2 = float(fleet["hour_profile"][i, 12:24].sum())
        for hh in range(horizon):
            td = as_of_date + dt.timedelta(days=hh + 1)
            rows.append((atm_id, as_of_date.isoformat(), td.isoformat(), hh + 1,
                         float(preds[i, hh, k50]), float(preds[i, hh, k90]),
                         float(preds[i, hh, k95]), p0, p1, p2))
    conn.execute("DELETE FROM forecasts WHERE as_of=?", (as_of_date.isoformat(),))
    conn.executemany(
        "INSERT INTO forecasts(atm_id,as_of,target_date,horizon,q50,q90,q95,"
        "p0_share,p1_share,p2_share) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
