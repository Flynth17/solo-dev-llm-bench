"""Granular V2-quality-corpus persistence (Act 11C-4B).

Stores *one completed* V2 quality result set -- the structured V2
quality-corpus result dict (suites + per-case granular evidence) -- into SQLite
WITHOUT touching any legacy schema, scoring or UI.

Design constraints honoured by this module:

* **Additive only.** Creates two dedicated tables -- ``v2_quality_runs`` (the
  run-level aggregate for one V2 result set) and ``v2_quality_results`` (one
  granular row per ``CaseResult``, linked to its parent by a stable
  ``run_id``). It never alters, renames or removes any column in the legacy
  ``runs`` table, never reads it, and never touches CSV.

* **Keeps three concepts distinct.** The run row records
  ``logical_cases_attempted`` (53), ``result_entries`` (141) and
  ``checks_passed`` / ``checks_total`` (166). Each child row additionally carries
  its own per-case ``checks_passed`` / ``checks_total`` so the aggregate can be
  recomputed by summation. These are *not* conflated with one another.

* **Stable ordering for repeated case_ids.** The Python suite reuses the
  same ``case_id`` across multiple iterations, so every child row carries a
  monotonic ``seq`` index that preserves the original order on read-back.

* **Idempotent schema creation / migration.** Follows the existing additive
  pattern in :mod:`src.results`: tables are created with ``CREATE TABLE IF NOT
  EXISTS`` and any missing columns gain an ``ALTER TABLE ... ADD COLUMN``
  guarded by a ``PRAGMA table_info`` presence check. Running it repeatedly
  against the same database file is safe.

* **No live execution wiring.** This module only moves structured data in and
  out of SQLite; it performs no LM Studio / subprocess calls and imports none of
  the frozen quality validators, so it cannot change their behaviour.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Default storage location mirrors src.results so a V2 result for a benchmark
# run can live alongside the legacy rows it describes (in a separate table).
_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
_DEFAULT_DB_PATH = _DEFAULT_DATA_DIR / "benchmark_results.db"


# Column definitions for the two new tables. Order and types are fixed; adding a
# column later only requires a guarded ALTER, exactly as done elsewhere.
V2_QUALITY_RUNS_COLUMNS = [
    ("id", "INTEGER"),
    ("run_id", "TEXT"),
    ("timestamp", "TEXT"),
    ("suite", "TEXT"),
    ("logical_cases_attempted", "INTEGER"),
    ("result_entries", "INTEGER"),
    ("checks_passed", "INTEGER"),
    ("checks_total", "INTEGER"),
    ("classification", "TEXT"),
    # Act 11C-4B1.1: additive configuration-identity linkage so a persisted V2
    # result can be unambiguously associated with the exact benchmark run's MODEL
    # + INFERENCE CONFIGURATION (recoverable without guessing / joining legacy).
    ("configuration_fingerprint", "TEXT"),
    ("model_identifier", "TEXT"),
]

V2_QUALITY_RESULTS_COLUMNS = [
    ("id", "INTEGER"),
    ("run_id", "TEXT"),
    ("seq", "INTEGER"),
    ("case_id", "TEXT"),
    ("category", "TEXT"),
    ("passed", "INTEGER"),
    ("checks_passed", "INTEGER"),
    ("checks_total", "INTEGER"),
    ("failure_type", "TEXT"),
    ("failure_reason", "TEXT"),
    ("expected", "TEXT"),
    ("actual", "TEXT"),
]


def _blank_or(value: Any) -> str:
    """Return a blank string for None/empty values (matches src.results convention)."""
    if value is None:
        return ""
    return str(value)


def _to_int_bool(value: Any) -> Optional[int]:
    """Normalise a boolean-ish ``passed`` flag to 0/1 (None stays None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _init_schema(conn: sqlite3.Connection) -> None:
    """Idempotently ensure both V2 tables exist with all known columns.

    Mirrors the additive migration strategy used in ``src.results``:
    create-if-not-exists, then guard every later-added column behind a
    ``PRAGMA table_info`` presence check so existing databases upgrade without
    losing data and re-running is always safe.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS v2_quality_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              TEXT UNIQUE,
            timestamp           TEXT,
            suite               TEXT,
            logical_cases_attempted  INTEGER,
            result_entries      INTEGER,
            checks_passed       INTEGER,
            checks_total        INTEGER,
            classification      TEXT,
            configuration_fingerprint  TEXT,
            model_identifier    TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS v2_quality_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT,
            seq             INTEGER,
            case_id         TEXT,
            category        TEXT,
            passed          INTEGER,
            checks_passed   INTEGER,
            checks_total    INTEGER,
            failure_type    TEXT,
            failure_reason  TEXT,
            expected        TEXT,
            actual          TEXT
        )
    """)

    for table, columns in (
        ("v2_quality_runs", V2_QUALITY_RUNS_COLUMNS),
        ("v2_quality_results", V2_QUALITY_RESULTS_COLUMNS),
    ):
        cols_cursor = conn.execute(f"PRAGMA table_info({table})")
        existing_cols = {row[1] for row in cols_cursor.fetchall()}
        # Skip the synthetic `id` column; it is always present from CREATE.
        for col, ctype in columns:
            if col == "id":
                continue
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype} DEFAULT ''")

    conn.commit()


class V2QualityStore:
    """Reads and writes the two additive V2-quality tables.

    The active legacy database (``data/benchmark_results.db``) may be used as a
    default, but every test constructs this with an isolated temporary path so no
    live run/task/CSV data is ever touched or polluted.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            _init_schema(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_run(
        self,
        v2_result: dict[str, Any],
        run_id: Optional[str] = None,
        configuration_fingerprint: Optional[str] = None,
        model_identifier: Optional[str] = None,
        classification: Optional[str] = None,
    ) -> str:
        """Persist one V2-quality result set and return its ``run_id``.

        The optional ``configuration_fingerprint`` / ``model_identifier`` (and the
        explicit ``classification`` override) record the Act 11B configuration
        identity of this run on the parent row only -- never per child row -- so a
        persisted V2 result can be unambiguously associated with the exact MODEL +
        INFERENCE CONFIGURATION that produced it. All are optional; when absent the
        row carries blank values and remains fully persistable on its own merits.

        Upserts the run-level aggregate row (keyed by ``run_id``) and replaces the
        child rows for that run in a single transaction, so repeated saves of the
        same run are idempotent rather than duplicating evidence. ``v2_result`` is
        never mutated.
        """
        if not isinstance(v2_result, dict):
            raise TypeError("v2_result must be a dict")

        rid = run_id or str(uuid.uuid4())
        results: list[dict[str, Any]] = v2_result.get("results", []) or []

        # Act 11C-4B1.1 configuration-identity linkage (additive). ``classification``
        # falls back to the corpus value when no explicit override is supplied.
        resolved_classification = classification
        if resolved_classification is None:
            resolved_classification = v2_result.get("classification")

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:  # commit on success / rollback on failure
                # Parent aggregate (one row per V2 result set).
                conn.execute(
                    """INSERT OR REPLACE INTO v2_quality_runs (
                           run_id, timestamp, suite,
                           logical_cases_attempted, result_entries,
                           checks_passed, checks_total, classification,
                        configuration_fingerprint, model_identifier
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rid,
                        datetime.now(timezone.utc).isoformat(),
                        _blank_or(v2_result.get("suite")),
                        v2_result.get("logical_cases_attempted"),
                        len(results),
                        v2_result.get("checks_passed"),
                        v2_result.get("checks_total"),
                        _blank_or(resolved_classification),
                        _blank_or(configuration_fingerprint),
                        _blank_or(model_identifier),
                    ),
                )

                # Replace child evidence rows for this run, preserving order.
                conn.execute("DELETE FROM v2_quality_results WHERE run_id = ?", (rid,))
                seq = 0
                for entry in results:
                    conn.execute(
                        """INSERT INTO v2_quality_results (
                               run_id, seq, case_id, category, passed,
                               checks_passed, checks_total, failure_type,
                               failure_reason, expected, actual
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            rid,
                            seq,
                            _blank_or(entry.get("case_id")),
                            _blank_or(entry.get("category")),
                            _to_int_bool(entry.get("passed")),
                            entry.get("checks_passed"),
                            entry.get("checks_total"),
                            _blank_or(entry.get("failure_type")),
                            _blank_or(entry.get("failure_reason")),
                            _blank_or(entry.get("expected")),
                            _blank_or(entry.get("actual")),
                        ),
                    )
                    seq += 1
        finally:
            conn.close()

        return rid

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a child row to its granular evidence dict (bool ``passed``)."""
        passed = row["passed"]
        return {
            "run_id": row["run_id"],
            "seq": row["seq"],
            "case_id": row["case_id"],
            "category": row["category"],
            "passed": bool(passed) if passed is not None else None,
            "checks_passed": row["checks_passed"],
            "checks_total": row["checks_total"],
            "failure_type": row["failure_type"] or "",
            "failure_reason": row["failure_reason"] or "",
            "expected": row["expected"] or "",
            "actual": row["actual"] or "",
        }

    def load_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Return one persisted V2 result set, or None when no such run exists.

        The returned dict carries ``run`` (the aggregate record) and ``results``
        (ordered granular evidence rows). ``passed`` flags are restored to real
        booleans so they compare equal to the original in-memory results.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM v2_quality_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None

            cur = conn.execute(
                "SELECT * FROM v2_quality_results WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            )
            results = [self._row_to_result(r) for r in cur.fetchall()]
        finally:
            conn.close()

        return {
            "run": dict(row),
            "results": results,
        }

    def load_all_runs(self) -> list[dict[str, Any]]:
        """Return every persisted V2 result set (most recent first)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT * FROM v2_quality_runs ORDER BY timestamp DESC, id DESC"
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def clear_run(self, run_id: str) -> bool:
        """Delete one V2 result set (parent + children); returns True if removed."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM v2_quality_runs WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            if count == 0:
                return False
            with conn:
                conn.execute("DELETE FROM v2_quality_results WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM v2_quality_runs WHERE run_id = ?", (run_id,))
        finally:
            conn.close()
        return True


def persist_v2_result(v2_result: dict[str, Any], db_path: Optional[Path] = None) -> str:
    """Convenience one-liner: persist ``v2_result`` and return its run_id."""
    store = V2QualityStore(db_path=db_path)
    return store.save_run(v2_result)


def read_v2_result(run_id: str, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Convenience one-liner: load a persisted V2 result set by run_id."""
    store = V2QualityStore(db_path=db_path)
    return store.load_run(run_id)
