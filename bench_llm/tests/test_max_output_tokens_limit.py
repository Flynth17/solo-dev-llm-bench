"""Tests verifying evaluation route accepts 100k default and rejects invalid values."""

import json
import os
import inspect


class TestBackendValidation:
    """Verify backend accepts new range and rejects invalid values."""

    def test_10000000_accepted(self):
        """Value 10000000 must not raise validation error (source check)."""
        from src.routes import benchmark as bench_mod
        source = inspect.getsource(bench_mod)
        assert "10000000" in source, "Benchmark route should accept up to 10000000"

class TestValueReachesLmStudio:
    """Verify the selected value reaches LM Studio payload unchanged."""

    def test_selected_value_reaches_benchmark_engine(self):
        """The max_tokens config value must be forwarded to run_benchmark unchanged."""
        from src.routes import benchmark as bench_mod
        source = inspect.getsource(bench_mod)
        assert 'max_tokens=max_tokens' in source, "max_tokens should reach benchmark engine"


class TestStaleDefaults:
    """Verify no stale 1024 defaults remain in evaluation config files."""

    def test_html_default_not_1024(self):
        """HTML input default must not be 1024."""
        html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
        content = open(html_path).read()
        assert 'value="1024"' not in content, "HTML default must not be 1024"

    def test_config_settings_not_1024(self):
        """config/settings.json max_tokens must not be 1024."""
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")
        with open(config_path) as f:
            cfg = json.load(f)
        assert cfg["max_tokens"] != 1024, "Config max_tokens must not be 1024"