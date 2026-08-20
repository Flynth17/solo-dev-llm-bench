"""Results storage for Solo Dev LLM Bench.

In-memory list of benchmark runs with SQLite persistence and CSV
migration/compatibility.
Old JSON results.json (if it exists) is left untouched.
"""

import csv
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
_DEFAULT_CSV_PATH = _DEFAULT_DATA_DIR / "benchmark_results.csv"
_DEFAULT_DB_PATH = _DEFAULT_DATA_DIR / "benchmark_results.db"

# CSV column headers
CSV_HEADERS = [
    "timestamp",
    "run_id",
    "model_key",
    "model_display_name",
    "model_quantization",
    "hardware_label",
    "execution_environment",
    "connection_type",
    "iteration",
    "cold_or_warm",
    "tokens_per_second",
    "ttft_seconds",
    "input_tokens",
    "output_tokens",
    "model_load_time_seconds",
    "wall_time_seconds",
    "prompt_name",
    "max_output_tokens",
    "temperature",
]

# SQLite column types matching CSV_HEADERS
SQLITE_COLUMNS = [
    ("timestamp", "TEXT"),
    ("run_id", "TEXT"),
    ("model_key", "TEXT"),
    ("model_display_name", "TEXT"),
    ("model_quantization", "TEXT"),
    ("hardware_label", "TEXT"),
    ("execution_environment", "TEXT"),
    ("connection_type", "TEXT"),
    ("iteration", "INTEGER"),
    ("cold_or_warm", "TEXT"),
    ("tokens_per_second", "REAL"),
    ("ttft_seconds", "REAL"),
    ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
    ("model_load_time_seconds", "REAL"),
    ("wall_time_seconds", "REAL"),
    ("prompt_name", "TEXT"),
    ("max_output_tokens", "INTEGER"),
    ("temperature", "REAL"),
]

# Blank placeholder for missing values
_BLANK = ""

# Migration tracking: simple flag to indicate CSV has been imported
_MIGRATION_FLAG_KEY = "csv_migrated"


def _blank_or(value):
    """Return blank string for None/empty values."""
    if value is None:
        return _BLANK
    return str(value)


class ResultsStore:
    """Manages benchmark results in memory and on disk.

    SQLite is the primary persistence layer.  CSV is preserved for
    backward-compatibility and migration purposes.
    """

    def __init__(
        self,
        csv_path: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.csv_path = csv_path or _DEFAULT_CSV_PATH
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.runs: list[dict] = []

        # Ensure directories exist
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure CSV header exists
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADERS)

        # Initialise SQLite database
        self._init_db()

        # Run CSV migration if needed
        self._migrate_csv_to_sqlite()

        # Load from SQLite (source of truth)
        self._load_from_db()

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Return a sqlite3 connection with row_factory set."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """Create the runs table if it does not exist."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    run_id TEXT,
                    model_key TEXT,
                    model_display_name TEXT,
                    model_quantization TEXT,
                    hardware_label TEXT,
                    execution_environment TEXT,
                    connection_type TEXT,
                    iteration INTEGER,
                    cold_or_warm TEXT,
                    tokens_per_second REAL,
                    ttft_seconds REAL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    model_load_time_seconds REAL,
                    wall_time_seconds REAL,
                    prompt_name TEXT,
                    max_output_tokens INTEGER,
                    temperature REAL
                )
            """)
            # Ensure the model_quantization column exists on databases created before this field.
            cols_cursor = conn.execute("PRAGMA table_info(runs)")
            existing_cols = {row[1] for row in cols_cursor.fetchall()}
            if "model_quantization" not in existing_cols:
                conn.execute(
                    "ALTER TABLE runs ADD COLUMN model_quantization TEXT DEFAULT ''"
                )
            conn.commit()
            # Metadata table for migration tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict with type conversion."""
        if row is None:
            return {}
        result = {}
        for key in row.keys():
            value = row[key]
            # NULL in SQLite -> None
            if value is None:
                result[key] = None
            else:
                result[key] = value
        return result

    def _load_from_db(self) -> None:
        """Load all runs from SQLite into memory."""
        self.runs = []
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM runs ORDER BY id ASC")
            for row in cursor:
                self.runs.append(self._row_to_dict(row))
        finally:
            conn.close()

    def _dict_to_values(self, run: dict) -> tuple:
        """Convert a dict to a tuple matching SQLITE_COLUMNS order."""
        values = []
        for col in CSV_HEADERS:
            v = run.get(col)
            if v is None or v == _BLANK:
                values.append(None)
            else:
                values.append(v)
        return tuple(values)

    # ------------------------------------------------------------------
    # CSV migration
    # ------------------------------------------------------------------

    def _migrate_csv_to_sqlite(self) -> None:
        """Import existing CSV rows into SQLite if not already migrated.

        Migration is idempotent: it uses a boolean flag stored in the
        metadata table.  Once the flag is set, the CSV is never read
        for import again.  New benchmark runs write to both SQLite
        (primary) and CSV (compatibility mirror).
        """
        # Check whether migration already happened
        migrated = self._get_migration_flag()
        if migrated:
            return

        # Count CSV data rows (excluding header)
        csv_row_count = 0
        if self.csv_path.exists():
            with open(self.csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for _ in reader:
                    csv_row_count += 1

        if csv_row_count == 0:
            # No CSV data to import — still mark as migrated.
            self._set_migration_flag(True)
            return

        # Import all CSV rows into SQLite.
        conn = self._get_connection()
        try:
            with open(self.csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    values = self._dict_to_values(row)
                    conn.execute(
                        f"INSERT INTO runs ({', '.join(CSV_HEADERS)}) "
                        f"VALUES ({','.join(['?'] * len(CSV_HEADERS))})",
                        values,
                    )
            conn.commit()
            # Mark migration as complete.
            self._set_migration_flag(True)
        finally:
            conn.close()

    def _get_migration_flag(self) -> bool:
        """Return True if CSV migration has already been performed."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (_MIGRATION_FLAG_KEY,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            return row["value"] == "1"
        finally:
            conn.close()

    def _set_migration_flag(self, value: bool) -> None:
        """Record that CSV migration has been performed."""
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (_MIGRATION_FLAG_KEY, "1" if value else "0"),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API (compatible with the old interface)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Legacy alias — now a no-op since loading happens in __init__."""
        pass

    @staticmethod
    def _parse_row(row: dict) -> dict:
        """Legacy CSV parser — kept for backward compatibility."""
        result = {}
        for key, value in row.items():
            if value == _BLANK or value is None:
                result[key] = None
                continue
            try:
                if "." in value:
                    result[key] = float(value)
                else:
                    result[key] = int(value)
            except ValueError:
                result[key] = value
        return result

    def save(self) -> None:
        """Persist all in-memory runs to SQLite (and CSV for compatibility)."""
        # Write to CSV for backward-compatibility
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            for run in self.runs:
                writer.writerow([run.get(h, _BLANK) for h in CSV_HEADERS])

        # Write to SQLite: replace all rows
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM runs")
            for run in self.runs:
                values = self._dict_to_values(run)
                conn.execute(
                    f"INSERT INTO runs ({', '.join(CSV_HEADERS)}) "
                    f"VALUES ({','.join(['?'] * len(CSV_HEADERS))})",
                    values,
                )
            conn.commit()
        finally:
            conn.close()

    def add_run(self, run_data: dict) -> None:
        """Append a single benchmark iteration and persist."""
        self.runs.append(run_data)
        self.save()

    def get_all(self) -> list[dict]:
        """Return all saved benchmark runs (source of truth)."""
        # Reload from SQLite to ensure freshness
        self._load_from_db()
        return self.runs

    def clear(self) -> None:
        """Clear all results from memory, SQLite, and CSV."""
        self.runs = []
        self.save()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_run(self, run_id: str) -> bool:
        """Delete all rows for a given run_id from SQLite and memory.

        Uses a transaction for safety.  Returns True if any rows were
        removed, False if the run_id was not found.
        """
        conn = self._get_connection()
        try:
            # Count rows before deletion (for return value)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE run_id = ?",
                (run_id,),
            )
            count = cursor.fetchone()[0]
            if count == 0:
                return False

            # Delete rows in a transaction
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.commit()
        finally:
            conn.close()

        # Remove from in-memory list
        self.runs = [r for r in self.runs if r.get("run_id") != run_id]
        return True
