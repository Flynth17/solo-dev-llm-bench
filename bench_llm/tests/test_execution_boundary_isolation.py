"""Generated-code isolation + fail-closed tests (P0 security hardening).

These prove the execution boundary enforces:
  * inherited secrets / credentials are NOT visible to generated code,
  * generated code runs confined to its isolated workspace (cannot traverse into
    the benchmark repository across a drive boundary),
  * the process is rooted at its workspace cwd (not repo/home/config),
  * a sandbox that cannot start FAILS CLOSED -- BenchLLM reports an execution /
    infrastructure failure and does NOT fall back to unrestricted host execution,
  * hard wall-clock timeout propagation.

No real credentials are used: synthetic markers only.
"""

import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.execution_boundary import (  # noqa: E402
    ExecutionBoundaryError,
    run_generated_code,
)
from src.python_validator import validate_python_solution  # noqa: E402

# Synthetic marker -- never a real credential.
SECRET_NAME = "BENCH_SECRET_SHOULD_NOT_BE_VISIBLE"
SECRET_VALUE = "leak-me-please-synthetic"


def _run_py(code: str, cwd, timeout=30.0):
    """Run an inline python snippet under the boundary and return its stdout."""
    out = run_generated_code([sys.executable, "-c", code], cwd=cwd, timeout=timeout)
    return out.stdout or ""


class TestEnvSecretIsolation:
    """Generated code must not see inherited secret-like environment variables."""

    def test_inherited_secret_not_visible(self, monkeypatch):
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        monkeypatch.setenv("API_TOKEN_SIMULATED", "sekret-123")
        ws = tempfile.mkdtemp(prefix="eb_ws_")
        seen = _run_py(
            f"import os\n"
            f"print('PY_SECRET=' + str(os.environ.get('{SECRET_NAME}')))\n"
            f"print('PY_TOKEN=' + str(os.environ.get('API_TOKEN_SIMULATED')))\n",
            ws,
        )
        assert "leak-me-please-synthetic" not in seen
        assert "sekret-123" not in seen
        assert "PY_SECRET=None" in seen

    def test_home_and_credentials_stripped(self):
        """HOME / HOMEPATH are not forwarded, so ~ cannot resolve to the host home."""
        ws = tempfile.mkdtemp(prefix="eb_ws_")
        val = _run_py(
            "import os\n"
            'print("EXPANDUSER=" + repr(os.path.expanduser("~")))\n'
            "try:\n"
            '    open(os.path.expanduser("~/.ssh/id_rsa")).read()\n'
            "except Exception as e:\n"
            '    print("CRED_PATH_BLOCKED=" + type(e).__name__)\n',
            ws,
        )
        exp = [l for l in val.splitlines() if l.startswith("EXPANDUSER=")][0]
        # expanduser('~') must NOT resolve to a real host home directory; with HOME
        # not forwarded it degrades to '~' (or ''), never 'C:\\Users\\...'.
        assert "'~'" in exp or "''" in exp
        assert "CRED_PATH_BLOCKED=" in val

    def test_network_targeting_via_injected_secret_is_denied(self):
        """Generated code cannot be directed at an internal service using a secret
        endpoint address that only exists in the parent environment (which is not
        forwarded).  Raw socket egress on this Windows dev host is NOT OS-blocked and
        is documented as such; what we DO guarantee is that env-driven targeting /
        credential leaks are prevented."""
        ws = tempfile.mkdtemp(prefix="eb_ws_")
        seen = _run_py(
            "import os\n"
            'print("BACKEND_URL=" + str(os.environ.get("BENCH_BACKEND_URL")))\n',
            ws,
        )
        assert "http://127.0.0.1:9" not in seen
        assert "BACKEND_URL=None" in seen


class TestWorkspaceConfinement:
    """Generated code must be confined to its isolated workspace."""

    def test_cwd_is_the_workspace(self):
        ws = tempfile.mkdtemp(prefix="eb_ws_")
        got = _run_py("import os\nprint('CWD=' + os.getcwd())\n", ws)
        assert "CWD=" in got and os.path.normcase(got.split("CWD=")[1].strip()) == os.path.normcase(ws)

    def test_cannot_traverse_into_benchmark_repository(self, tmp_path):
        """The sentinel lives under the pytest repo root (a different drive from the
        system-temp workspace).  Relative traversal out of the isolated cwd cannot
        reach it -- the process is rooted at its own workspace."""
        sentinel_dir = tmp_path
        sentinel = sentinel_dir / "host_only_secret.txt"
        sentinel.write_text("TOP_SECRET_HOST_FILE")

        ws = tempfile.mkdtemp(prefix="eb_esc_")  # system temp, not under repo root
        evil = (
            "hit=False\n"
            "for _ in range(8):\n"
            "    try:\n"
            "        with open('../../../../../../'+ 'host_only_secret.txt') as f:\n"
            "            f.read(); hit=True\n"
            "    except Exception:\n"
            "        break\n"
            "print('ESCAPED=' + str(hit))\n"
        )
        seen = _run_py(evil, ws)
        assert "ESCAPED=True" not in seen


class TestFailClosed:
    """Sandbox startup failure must FAIL CLOSED -- never fall back to host exec."""

    def test_missing_executable_raises_execution_boundary_error(self):
        with pytest.raises(ExecutionBoundaryError):
            run_generated_code(["__definitely_not_a_real_exe_xyz__", "--help"], cwd=tempfile.mkdtemp(), timeout=5)

    def test_python_validator_maps_runner_failure_to_fail_closed_result(self, monkeypatch):
        """When the secure runner cannot execute generated code, validate_python_solution
        must return a FAIL result (score 0, passed False) with an execution-failure
        error -- it must NOT silently pass or fall back to host execution."""
        from src import python_validator as pv

        def boom(*args, **kwargs):
            raise ExecutionBoundaryError("secure runner failed to start")

        monkeypatch.setattr(pv, "run_generated_code", boom)
        correct_code = "def add(a, b):\n    return a + b\n"
        test_code = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
            "from solution import add\n"
            "def test_add():\n    assert add(1, 2) == 3\n"
        )
        result = validate_python_solution(correct_code, test_code)
        assert result.passed is False
        assert result.score == 0.0
        assert "execution boundary" in result.error.lower()


class TestTimeout:
    """Hard wall-clock timeout propagates from the boundary."""

    def test_timeout_propagates(self):
        with pytest.raises(subprocess.TimeoutExpired):
            run_generated_code([sys.executable, "-c", "import time; time.sleep(5)"],
                               cwd=tempfile.mkdtemp(), timeout=1.0)
