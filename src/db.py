"""SQLite access layer."""
from __future__ import annotations

import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schema.sql")


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return rows_to_dicts(conn.execute(sql, params).fetchall())


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = (), default=None):
    row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def insert_many(conn: sqlite3.Connection, sql: str, records) -> None:
    conn.executemany(sql, records)


def set_metric(conn, policy: str, metric: str, value=None, text_value=None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sim_metrics(policy, metric, value, text_value) "
        "VALUES (?,?,?,?)",
        (policy, metric, value, text_value),
    )


def get_metrics(conn, policy: str) -> dict:
    out = {}
    for r in conn.execute(
        "SELECT metric, value, text_value FROM sim_metrics WHERE policy=?", (policy,)
    ):
        out[r["metric"]] = r["text_value"] if r["value"] is None else r["value"]
    return out


def json_dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def json_loads(text: str):
    return json.loads(text)
