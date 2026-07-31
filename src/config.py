"""Configuration loading and validation (Requirement 16)."""
from __future__ import annotations

import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")

# (key, low, high) inclusive bounds used for validation (Req 16.6).
_BOUNDS = [
    ("n_atms", 1, 100000),
    ("history_days", 60, 5000),
    ("telemetry_days", 1, 5000),
    ("sim_days", 1, 2000),
    ("notes_per_cartridge", 1, 100000),
    ("cartridges_per_atm", 1, 12),
    ("daily_trip_cap", 1, 100000),
    ("adhoc_reserve_trips", 0, 100000),
    ("max_refill_gap_days", 1, 365),
    ("refill_eligibility_pct", 0.0, 1.0),
    ("low_cash_threshold_pct", 0.0, 1.0),
    ("safety_quantile", 0.5, 0.999),
    ("planning_horizon_days", 1, 365),
    ("adhoc_response_lag_hours", 0, 168),
    ("fixed_calendar_interval_days", 1, 365),
]


class ConfigError(ValueError):
    """Raised when configuration is structurally or semantically invalid."""


class Config:
    def __init__(self, data: dict):
        self._data = data
        self.validate()

    # -- access -------------------------------------------------------------
    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    @property
    def raw(self) -> dict:
        return dict(self._data)

    def as_json(self) -> str:
        return json.dumps(self._data, sort_keys=True)

    # -- derived ------------------------------------------------------------
    @property
    def effective_daily_cap(self) -> int:
        """Planned-trip capacity after reserving headroom for ad hoc trips (Req 7.2)."""
        return int(self["daily_trip_cap"]) - int(self["adhoc_reserve_trips"])

    def window_start_hour(self, window: str) -> int:
        return int(self["window_start_hour"][window])

    # -- validation ---------------------------------------------------------
    def validate(self) -> None:
        d = self._data
        for key, low, high in _BOUNDS:
            if key not in d:
                raise ConfigError("missing required config key: %s" % key)
            val = d[key]
            if not isinstance(val, (int, float)):
                raise ConfigError("config key %s must be numeric, got %r" % (key, val))
            if not (low <= val <= high):
                raise ConfigError(
                    "config key %s = %r out of bounds [%s, %s]" % (key, val, low, high)
                )

        # Req 16.3: eligibility must sit strictly above the low-cash floor.
        elig = float(d["refill_eligibility_pct"])
        floor = float(d["low_cash_threshold_pct"])
        if elig <= floor:
            raise ConfigError(
                "refill_eligibility_pct (%.3f) must be strictly greater than "
                "low_cash_threshold_pct (%.3f)" % (elig, floor)
            )

        mix = d.get("priority_mix") or {}
        for band in ("P0", "P1", "P2"):
            if band not in mix:
                raise ConfigError("priority_mix missing band %s" % band)
        total = sum(float(mix[b]) for b in ("P0", "P1", "P2"))
        if abs(total - 1.0) > 1e-9:
            raise ConfigError("priority_mix must sum to 1.0, got %.6f" % total)

        starts = d.get("window_start_hour") or {}
        for band in ("P0", "P1", "P2"):
            if band not in starts:
                raise ConfigError("window_start_hour missing band %s" % band)
        if not (starts["P0"] < starts["P1"] < starts["P2"]):
            raise ConfigError("window_start_hour must increase across P0 < P1 < P2")

        if self.effective_daily_cap < 1:
            raise ConfigError(
                "adhoc_reserve_trips (%s) leaves no planned capacity under "
                "daily_trip_cap (%s)" % (d["adhoc_reserve_trips"], d["daily_trip_cap"])
            )

        qs = d.get("forecast_quantiles") or []
        if not qs:
            raise ConfigError("forecast_quantiles must be a non-empty list")
        for q in qs:
            if not (0.0 < float(q) < 1.0):
                raise ConfigError("forecast quantile out of range: %r" % q)
        if float(d["safety_quantile"]) not in [float(q) for q in qs]:
            raise ConfigError(
                "safety_quantile %r must be one of forecast_quantiles %r"
                % (d["safety_quantile"], qs)
            )

    def db_path(self) -> str:
        p = self["db_path"]
        return p if os.path.isabs(p) else os.path.join(REPO_ROOT, p)


def load_config(path: str | None = None, overrides: dict | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                merged = dict(data[k])
                merged.update(v)
                data[k] = merged
            else:
                data[k] = v
    return Config(data)