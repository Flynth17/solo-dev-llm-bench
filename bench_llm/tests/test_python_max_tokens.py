"""Tests verifying Python correctness task respects max_tokens from dashboard.

Proves that the route correctly maps the dashboard's `max_tokens` config key
to the Python runner's `max_output_tokens` parameter.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock


# ------------------------------------------------------------------
# Test: max_tokens forwarding in the Python task branch
# ------------------------------------------------------------------

class TestPythonMaxTokensForwarding:
    """Verify config['max_tokens'] -> run_python_correctness_task(max_output_tokens=...)."""

    def test_dashboard_value_1024_forwards_to_runner(self):
        """Dashboard max_tokens=1024 must reach the Python runner as max_output_tokens=1024."""
        import importlib
        import src.routes.tasks as tasks_mod
        importlib.reload(tasks_mod)

        mock_task = {
            "task_id": "test-py-1024",
            "task_type": "python",
            "name": "Python Correctness",
            "status": "pending",
        }

        mock_result = {"task_name": "Python Correctness", "task_type": "python", "score": 1.0, "passed": True}

        with patch.object(tasks_mod.task_manager, "get_task", return_value=mock_task), \
             patch.object(tasks_mod.task_manager, "update_task_status"), \
             patch.object(tasks_mod.task_manager, "set_task_result"), \
             patch.object(tasks_mod.task_manager, "create_task_run"), \
             patch.object(tasks_mod, "run_python_correctness_task", new_callable=AsyncMock) as mock_run, \
             patch("src.app_state.results_store"):

            mock_run.return_value = mock_result

            # Simulate dashboard request with max_tokens=1024
            config = {
                "model": "test-model",
                "lm_studio_url": "http://localhost:1234",
                "max_tokens": 1024,
                "temperature": 0,
                "iterations": 3,
                "hardware_label": "",
                "execution_environment": "Local",
                "connection_type": "Local network",
            }

            asyncio.run(tasks_mod.run_task("test-py-1024", config))

            # Verify the runner received max_output_tokens=1024
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["max_output_tokens"] == 1024

    def test_dashboard_value_2048_forwards_to_runner(self):
        """Dashboard max_tokens=2048 must reach the Python runner as max_output_tokens=2048."""
        import importlib
        import src.routes.tasks as tasks_mod
        importlib.reload(tasks_mod)

        mock_task = {
            "task_id": "test-py-2048",
            "task_type": "python",
            "name": "Python Correctness",
            "status": "pending",
        }

        mock_result = {"task_name": "Python Correctness", "task_type": "python", "score": 1.0, "passed": True}

        with patch.object(tasks_mod.task_manager, "get_task", return_value=mock_task), \
             patch.object(tasks_mod.task_manager, "update_task_status"), \
             patch.object(tasks_mod.task_manager, "set_task_result"), \
             patch.object(tasks_mod.task_manager, "create_task_run"), \
             patch.object(tasks_mod, "run_python_correctness_task", new_callable=AsyncMock) as mock_run, \
             patch("src.app_state.results_store"):

            mock_run.return_value = mock_result

            config = {
                "model": "test-model",
                "lm_studio_url": "http://localhost:1234",
                "max_tokens": 2048,
                "temperature": 0,
                "iterations": 3,
                "hardware_label": "",
                "execution_environment": "Local",
                "connection_type": "Local network",
            }

            asyncio.run(tasks_mod.run_task("test-py-2048", config))

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["max_output_tokens"] == 2048

    def test_python_task_default_when_max_tokens_absent(self):
        """If max_tokens is absent from config, Python task should use route default (500)."""
        import importlib
        import src.routes.tasks as tasks_mod
        importlib.reload(tasks_mod)

        mock_task = {
            "task_id": "test-py-default",
            "task_type": "python",
            "name": "Python Correctness",
            "status": "pending",
        }

        mock_result = {"task_name": "Python Correctness", "task_type": "python", "score": 1.0, "passed": True}

        with patch.object(tasks_mod.task_manager, "get_task", return_value=mock_task), \
             patch.object(tasks_mod.task_manager, "update_task_status"), \
             patch.object(tasks_mod.task_manager, "set_task_result"), \
             patch.object(tasks_mod.task_manager, "create_task_run"), \
             patch.object(tasks_mod, "run_python_correctness_task", new_callable=AsyncMock) as mock_run, \
             patch("src.app_state.results_store"):

            mock_run.return_value = mock_result

            # Config without max_tokens key -- route defaults to 500 (its own default)
            config = {
                "model": "test-model",
                "lm_studio_url": "http://localhost:1234",
                "temperature": 0,
                "iterations": 3,
                "hardware_label": "",
                "execution_environment": "Local",
                "connection_type": "Local network",
            }

            asyncio.run(tasks_mod.run_task("test-py-default", config))

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            # Route line 84: max_tokens = int(config.get("max_tokens", 500))
            # So the fallback is 500 (not PY_TASK_DEF["max_output_tokens"])
            assert call_kwargs["max_output_tokens"] == 500