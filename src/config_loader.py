"""Configuration loader for bench_llm."""

import json
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from settings.json."""
    path = config_path or _DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict, config_path: Path | None = None) -> None:
    """Save configuration to settings.json."""
    path = config_path or _DEFAULT_CONFIG_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)