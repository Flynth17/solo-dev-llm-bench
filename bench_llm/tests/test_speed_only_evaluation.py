"""Tests verifying speed-only and correctness-only evaluation runs work correctly."""

import os


class TestSpeedOnlyRuns:
    """Verify speed-only evaluations are accepted."""

    def test_small_only_accepted(self):
        """Small-only speed run must be accepted (HTTP 200 or 502 from LM Studio, not 400)."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["small"],
            "correctness_tests": [],
            "iterations": 1,
            "max_output_tokens": 500,
            "temperature": 0,
        })
        # Should NOT be a validation error (400) — LM Studio connection may fail but that's OK
        assert resp.status_code != 400 or "At least one" not in str(resp.json().get("detail", ""))

    def test_medium_only_accepted(self):
        """Medium-only speed run must be accepted."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["medium"],
            "correctness_tests": [],
            "iterations": 1,
            "max_output_tokens": 500,
            "temperature": 0,
        })
        assert resp.status_code != 400 or "At least one" not in str(resp.json().get("detail", ""))

    def test_large_only_accepted(self):
        """Large-only speed run must be accepted."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["large"],
            "correctness_tests": [],
            "iterations": 1,
            "max_output_tokens": 500,
            "temperature": 0,
        })
        assert resp.status_code != 400 or "At least one" not in str(resp.json().get("detail", ""))


class TestCorrectnessOnlyRuns:
    """Verify correctness-only evaluations are accepted."""

    def test_markdown_only_accepted(self):
        """Markdown-only correctness run must be accepted (not rejected as 'must not be empty')."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": [],
            "correctness_tests": ["markdown"],
            "iterations": 1,
            "max_output_tokens": 500,
            "temperature": 0,
        })
        # Should NOT be rejected with "At least one" or "must not be empty" error
        assert resp.status_code != 400 or ("At least one" not in str(resp.json().get("detail", "")) and "must not be empty" not in str(resp.json().get("detail", "")))


class TestEmptyEvaluationRejected:
    """Verify no tests selected is rejected."""

    def test_empty_speed_and_correctness_rejected(self):
        """No speed AND no correctness must return HTTP 400 with validation error."""
        from fastapi.testclient import TestClient
        from src.routes.evaluation import router as evaluation_router

        app = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
        app.add_api_route("/api/evaluation/run", evaluation_router.routes[0].endpoint, methods=["POST"])
        client = TestClient(app)

        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": [],
            "correctness_tests": [],
            "iterations": 1,
            "max_output_tokens": 500,
            "temperature": 0,
        })
        assert resp.status_code == 400
        detail = str(resp.json().get("detail", ""))
        assert "At least one" in detail or "must be selected" in detail


class TestSpeedPromptFixturesUnchanged:
    """Verify speed prompt fixtures remain unchanged."""

    def test_small_prompt_unchanged(self):
        """Small prompt fixture must still exist and contain original content."""
        fixture_path = os.path.join(os.path.dirname(__file__), "..", "tasks", "speed_prompts", "small.md")
        assert os.path.isfile(fixture_path), "small.md fixture must exist"
        content = open(fixture_path).read()
        # Must still contain the deploy_helper.py function signature
        assert "check_services" in content, "Small prompt must retain original content"

    def test_medium_prompt_unchanged(self):
        """Medium prompt fixture must still exist."""
        fixture_path = os.path.join(os.path.dirname(__file__), "..", "tasks", "speed_prompts", "medium.md")
        assert os.path.isfile(fixture_path), "medium.md fixture must exist"

    def test_large_prompt_unchanged(self):
        """Large prompt fixture must still exist."""
        fixture_path = os.path.join(os.path.dirname(__file__), "..", "tasks", "speed_prompts", "large.md")
        assert os.path.isfile(fixture_path), "large.md fixture must exist"


class TestSpeedOutputTokensFixed:
    """Verify speed tests use fixed 1024 output tokens."""

    def test_speed_uses_fixed_output_tokens(self):
        """Speed tests must use SPEED_OUTPUT_TOKENS constant (1024)."""
        from src.routes import evaluation as eval_mod
        source = open(eval_mod.__file__).read()
        assert "SPEED_OUTPUT_TOKENS" in source, "Must define SPEED_OUTPUT_TOKENS"
        assert "= 1024" in source, "SPEED_OUTPUT_TOKENS must equal 1024"

    def test_speed_max_tokens_uses_constant(self):
        """run_benchmark call for speed tests must use SPEED_OUTPUT_TOKENS."""
        from src.routes import evaluation as eval_mod
        source = open(eval_mod.__file__).read()
        assert "max_tokens=SPEED_OUTPUT_TOKENS" in source, "Speed benchmark must use SPEED_OUTPUT_TOKENS"