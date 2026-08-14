"""Results storage for Solo Dev LLM Bench.

In-memory list of benchmark runs with CSV persistence.
Old JSON results.json (if it exists) is left untouched.
"""

import csv
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
_DEFAULT_CSV_PATH = _DEFAULT_DATA_DIR / "benchmark_results.csv"

# CSV column headers
CSV_HEADERS = [
    "timestamp",
    "run_id",
    "model_key",
    "model_display_name",
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

# Blank placeholder for missing values
_BLANK = ""


def _blank_or(value):
    """Return blank string for None/empty values."""
    if value is None:
        return _BLANK
    return str(value)


class ResultsStore:
    """Manages benchmark results in memory and on disk (CSV)."""

    def __init__(self, csv_path: Path | None = None) -> None:
        self.path = csv_path or _DEFAULT_CSV_PATH
        self.runs: list[dict] = []
        # Ensure data/ directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure CSV header exists
        if not self.path.exists():
            with open(self.path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADERS)
        self._load()

    def _load(self) -> None:
        """Load existing results from disk."""
        if not self.path.exists():
            return
        with open(self.path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert numeric fields back to numbers where possible
                parsed = self._parse_row(row)
                self.runs.append(parsed)

    @staticmethod
    def _parse_row(row: dict) -> dict:
        """Parse a CSV row back to appropriate types."""
        result = {}
        for key, value in row.items():
            if value == _BLANK or value is None:
                result[key] = None
                continue
            # Try numeric conversion
            try:
                if "." in value:
                    result[key] = float(value)
                else:
                    result[key] = int(value)
            except ValueError:
                result[key] = value
        return result

    def save(self) -> None:
        """Persist all runs to disk (rewrites CSV)."""
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            for run in self.runs:
                writer.writerow([run.get(h, _BLANK) for h in CSV_HEADERS])

    def add_run(self, run_data: dict) -> None:
        """Append a single benchmark iteration and persist."""
        self.runs.append(run_data)
        self.save()

    def get_all(self) -> list[dict]:
        """Return all saved benchmark runs."""
        return self.runs

    def clear(self) -> None:
        """Clear all results from memory and disk."""
        self.runs = []
        self.save()
