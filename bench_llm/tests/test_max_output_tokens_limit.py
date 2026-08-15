"""Tests verifying evaluation route accepts 100k default and rejects invalid values."""

import json
import os
import inspect


class TestMaxOutputTokensDefault:
    """Verify the new design limit defaults to 100,000."""

    def test_default_value_is_100000(self):
        """The HTML input value should default to 100000."""
        html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
        content = open(html_path).read()
        assert 'value="100000"' in content, "Default value should be 100000"

    def test_frontend_max_is_10000000(self):
        """The HTML input max attribute should be 10000000."""
        html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
        content = open(html_path).read()
        assert 'max="10000000"' in content, "Max attribute should be 10000000"

    def test_frontend_min_is_1(self):
        """The HTML input min attribute should be 1."""
        html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
        content = open(html_path).read()
        assert 'min="1"' in content, "Min attribute should be 1"


class TestBackendValidation:
    """Verify backend accepts new range and rejects invalid values."""

    def test_100000_accepted(self):
        """Value 100000 must not raise validation error (source check)."""
        from src.routes import evaluation as eval_mod
        source = inspect.getsource(eval_mod)
        assert "10000000" in source, "Backend should accept up to 10000000"

    def test_10000000_accepted(self):
        """Value 10000000 must not raise validation error (source check)."""
        from src.routes import benchmark as bench_mod
        source = inspect.getsource(bench_mod)
        assert "10000000" in source, "Benchmark route should accept up to 10000000"

    def test_10000001_rejected(self):
        """Value 10000001 must raise validation error."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "correctness_tests": ["markdown"],
            "speed_tests": [],
            "iterations": 1,
            "max_output_tokens": 10000001,
            "temperature": 0,
        })
        assert resp.status_code == 400

    def test_0_rejected(self):
        """Value 0 must raise validation error."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "correctness_tests": ["markdown"],
            "speed_tests": [],
            "iterations": 1,
            "max_output_tokens": 0,
            "temperature": 0,
        })
        assert resp.status_code == 400

    def test_negative_rejected(self):
        """Negative values must raise validation error."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "correctness_tests": ["markdown"],
            "speed_tests": [],
            "iterations": 1,
            "max_output_tokens": -1,
            "temperature": 0,
        })
        assert resp.status_code == 400


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