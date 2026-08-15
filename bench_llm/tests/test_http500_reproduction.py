"""Regression test for HTTP 500 on evaluation route with markdown correctness."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.main import app


client = TestClient(app)


class TestMarkdownCorrectnessHttp500:
    """Reproduce the HTTP 500 when running markdown correctness evaluation."""

    def test_markdown_correctness_no_crash(self):
        """Test that markdown correctness does not raise an unhandled exception.

        This reproduces the scenario where a user selects only 'markdown' as
        correctness_tests and clicks Run Evaluation.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": "# Fixed Document\n\nThis is fixed.",
            "stats": {"input_tokens": 10, "total_output_tokens": 5},
        }

        async def mock_post(*args, **kwargs):
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post("/api/evaluation/run", json={
                "lm_studio_url": "http://127.0.0.1:1234",
                "model": "test-model",
                "execution_environment": "Local",
                "connection_type": "",
                "hardware_label": "",
                "iterations": 1,
                "max_output_tokens": 500,
                "temperature": 0,
                "speed_tests": [],
                "correctness_tests": ["markdown"],
            })

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] == "completed"
        assert len(body["correctness_results"]) == 1
        assert body["correctness_results"][0]["test_type"] == "markdown"

    def test_python_correctness_no_crash(self):
        """Test that python correctness does not raise an unhandled exception."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": "def add(a, b):\n    return a + b",
            "stats": {"input_tokens": 10, "total_output_tokens": 5},
        }

        async def mock_post(*args, **kwargs):
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post("/api/evaluation/run", json={
                "lm_studio_url": "http://127.0.0.1:1234",
                "model": "test-model",
                "execution_environment": "Local",
                "connection_type": "",
                "hardware_label": "",
                "iterations": 1,
                "max_output_tokens": 500,
                "temperature": 0,
                "speed_tests": [],
                "correctness_tests": ["python"],
            })

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] == "completed"
        assert len(body["correctness_results"]) == 1
        assert body["correctness_results"][0]["test_type"] == "python"

    def test_combined_markdown_python_java_no_crash(self):
        """Test that combined correctness tests do not raise an unhandled exception."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": "# Fixed",
            "stats": {"input_tokens": 10, "total_output_tokens": 5},
        }

        async def mock_post(*args, **kwargs):
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post("/api/evaluation/run", json={
                "lm_studio_url": "http://127.0.0.1:1234",
                "model": "test-model",
                "execution_environment": "Local",
                "connection_type": "",
                "hardware_label": "",
                "iterations": 1,
                "max_output_tokens": 500,
                "temperature": 0,
                "speed_tests": [],
                "correctness_tests": ["markdown", "python", "java"],
            })

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] == "completed"
        assert len(body["correctness_results"]) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])