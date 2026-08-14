"""Task management for Solo Dev LLM Bench.

Stores markdown/Python/Java benchmark tasks in SQLite alongside the
existing runs table.  Tasks are linked to results via ``task_id``.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "data" / "benchmark_results.db"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

def init_tasks_table() -> None:
    """Ensure the ``tasks`` table exists (idempotent)."""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       TEXT    UNIQUE NOT NULL,
                name          TEXT    NOT NULL,
                task_type     TEXT    NOT NULL,     -- 'markdown' | 'python' | 'java'
                status        TEXT    NOT NULL,     -- 'pending' | 'running' | 'completed' | 'failed'
                prompt        TEXT,
                config        TEXT,              -- JSON blob
                result        TEXT,              -- JSON blob (populated on completion)
                created_at    TEXT    NOT NULL,
                started_at    TEXT,
                completed_at  TEXT,
                run_id        TEXT               -- links to a benchmark run when available
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def create_task(
    name: str,
    task_type: str,
    prompt: str = "",
    config: dict | None = None,
) -> dict:
    """Create a new benchmark task and return it."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task-{name[:20]}-{len(_list_all()) + 1:03d}"
    row: dict[str, Any] = {
        "task_id": task_id,
        "name": name,
        "task_type": task_type,
        "status": "pending",
        "prompt": prompt,
        "config": config or {},
        "result": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "run_id": None,
    }
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO tasks (task_id, name, task_type, status, prompt, config,
                               created_at, started_at, completed_at, result, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["task_id"],
                row["name"],
                row["task_type"],
                row["status"],
                row["prompt"],
                _json(row["config"]),
                row["created_at"],
                row["started_at"],
                row["completed_at"],
                row["result"],
                row["run_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return row


def _list_all() -> list[dict]:
    conn = _get_conn()
    try:
        cursor = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        return [dict(r) for r in cursor]
    finally:
        conn.close()


def get_tasks() -> list[dict]:
    """Return all tasks, newest first."""
    init_tasks_table()
    return _list_all()


def get_task(task_id: str) -> dict | None:
    conn = _get_conn()
    try:
        cursor = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_task_status(task_id: str, status: str, run_id: str | None = None) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    if status == "running":
        col, val = "started_at", now
    elif status in ("completed", "failed"):
        col, val = "completed_at", now
    else:
        col, val = "status", status
    conn = _get_conn()
    try:
        if run_id is not None:
            conn.execute(
                "UPDATE tasks SET status = ?, started_at = ?, completed_at = ?, run_id = ? WHERE task_id = ?",
                (status, now, now, run_id, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status = ?, started_at = ?, completed_at = ? WHERE task_id = ?",
                (status, now, now, task_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id)


def set_task_result(task_id: str, result: dict) -> dict | None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET result = ?, status = 'completed', completed_at = ? WHERE task_id = ?",
            (_json(result), datetime.now(timezone.utc).isoformat(), task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj)