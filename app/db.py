"""Thin SQLite repository layer (stdlib sqlite3, no ORM).

Connections are reused per (thread, db_path) via a thread-local pool. Opening a
fresh SQLite connection — with PRAGMA setup — on every call is expensive under
ingestion load (the generator alone drives thousands of inserts per minute), so
each thread keeps one long-lived connection per database path. Keying on path
preserves isolation when tests point at different databases. ``check_same_thread``
is disabled and writes commit explicitly; WAL mode keeps reads/writes concurrent.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_local = threading.local()


def _conn() -> sqlite3.Connection:
    db_path = get_settings().db_path
    pool: dict[str, sqlite3.Connection] = getattr(_local, "pool", None)
    if pool is None:
        pool = {}
        _local.pool = pool
    conn = pool.get(db_path)
    if conn is None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")  # wait out concurrent writers
        pool[db_path] = conn
    return conn


def init_db() -> None:
    """Create tables idempotently from schema.sql."""
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn = _conn()
    conn.executescript(schema)
    conn.commit()


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# --- logs -----------------------------------------------------------------

def insert_log(
    ts: str, service: str, level: str, message: str,
    latency_ms: float | None, raw: dict[str, Any] | None = None,
) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO logs (ts, service, level, message, latency_ms, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, service, level, message, latency_ms,
         json.dumps(raw) if raw is not None else None),
    )
    conn.commit()
    return int(cur.lastrowid)


def count_logs() -> int:
    return int(_conn().execute("SELECT COUNT(*) FROM logs").fetchone()[0])


def recent_logs(service: str, limit: int = 15) -> list[dict[str, Any]]:
    """Most-recent raw log lines for a service (newest first) — used to give the
    LLM classifier sample context for an anomalous window."""
    rows = _conn().execute(
        "SELECT level, message, latency_ms FROM logs WHERE service = ? "
        "ORDER BY id DESC LIMIT ?", (service, limit)
    ).fetchall()
    return _rows_to_dicts(rows)


# --- windows --------------------------------------------------------------

def insert_window(w: dict[str, Any]) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO windows (service, bucket_start, bucket_end, count, "
        "error_count, error_rate, latency_mean, latency_p95, latency_std) "
        "VALUES (:service, :bucket_start, :bucket_end, :count, :error_count, "
        ":error_rate, :latency_mean, :latency_p95, :latency_std)",
        w,
    )
    conn.commit()
    return int(cur.lastrowid)


def count_windows() -> int:
    return int(_conn().execute("SELECT COUNT(*) FROM windows").fetchone()[0])


def list_windows(limit: int = 600) -> list[dict[str, Any]]:
    """Most-recent windows, returned in chronological (ascending) order."""
    rows = _conn().execute(
        "SELECT * FROM (SELECT * FROM windows ORDER BY id DESC LIMIT ?) "
        "ORDER BY id ASC", (limit,)
    ).fetchall()
    return _rows_to_dicts(rows)


# --- anomalies ------------------------------------------------------------

def insert_anomaly(a: dict[str, Any]) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO anomalies (ts, service, window_id, score, method, "
        "features_json, explanation, severity, category) "
        "VALUES (:ts, :service, :window_id, :score, :method, :features_json, "
        ":explanation, :severity, :category)",
        a,
    )
    conn.commit()
    return int(cur.lastrowid)


def list_anomalies(limit: int = 50, service: str | None = None,
                   severity: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM anomalies"
    clauses, params = [], []
    if service:
        clauses.append("service = ?")
        params.append(service)
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return _rows_to_dicts(_conn().execute(q, params).fetchall())


def count_anomalies() -> int:
    return int(_conn().execute("SELECT COUNT(*) FROM anomalies").fetchone()[0])


def update_anomaly_enrichment(anomaly_id: int, category: str | None,
                              severity: str, explanation: str) -> None:
    """Persist LLM enrichment back onto an anomaly row."""
    conn = _conn()
    conn.execute(
        "UPDATE anomalies SET category = ?, severity = ?, explanation = ? "
        "WHERE id = ?", (category, severity, explanation, anomaly_id))
    conn.commit()


# --- alerts ---------------------------------------------------------------

def insert_alert(a: dict[str, Any]) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO alerts (anomaly_id, ts, status, target_url, "
        "payload_json, response_json) "
        "VALUES (:anomaly_id, :ts, :status, :target_url, :payload_json, "
        ":response_json)",
        a,
    )
    conn.commit()
    return int(cur.lastrowid)


def list_alerts(limit: int = 50) -> list[dict[str, Any]]:
    return _rows_to_dicts(
        _conn().execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    )


def count_alerts() -> int:
    return int(_conn().execute("SELECT COUNT(*) FROM alerts").fetchone()[0])
