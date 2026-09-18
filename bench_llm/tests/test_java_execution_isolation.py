"""Java generated-code isolation + fail-closed tests (P0 security hardening).

Equivalent guarantees to the Python boundary, exercised through the canonical
subprocess/javac path used by the benchmark. Synthetic markers only -- no real
credentials.
"""

import os
import pytest
import sys
import tempfile

# Executes real external toolchains via subprocess (javac/java). Excluded from
# the fast gate via `-m "not integration"` to stay JDK/toolchain-free and
# deterministic; retained in the full regression suite.
pytestmark = pytest.mark.integration

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.execution_boundary import ExecutionBoundaryError, run_generated_code  # noqa: E402

SECRET_NAME = "BENCH_SECRET_SHOULD_NOT_BE_VISIBLE"


def _java_main_src() -> str:
    return (
        "public class Probe {\n"
        "    public static void main(String[] a) {\n"
        f"        System.out.println(\"ENV=\" + System.getenv(\"{SECRET_NAME}\"));\n"
        '        try {\n'
        '            java.nio.file.Files.readString(java.nio.file.Path.of("~/.ssh/id_rsa"));\n'
        '            System.out.println("CRED_OK");\n'
        "        } catch (Exception e) {\n"
        '            System.out.println("CRED_BLOCKED=" + e.getClass().getSimpleName());\n'
        "        }\n"
        "    }\n"
        "}\n"
    )


def _compile_and_run(src: str, cwd):
    sol = os.path.join(cwd, "Probe.java")
    with open(sol, "w", encoding="utf-8") as fh:
        fh.write(src)
    compile_out = run_generated_code(["javac", "Probe.java"], cwd=cwd, timeout=30)
    if compile_out.exit_code != 0:
        raise AssertionError("java compile failed under boundary: " + (compile_out.stderr or ""))
    return run_generated_code(["java", "Probe"], cwd=cwd, timeout=30)


class TestJavaEnvAndCredentialIsolation:
    def test_inherited_secret_not_visible_to_java(self):
        ws = tempfile.mkdtemp(prefix="eb_java_ws_")
        out = _compile_and_run(_java_main_src(), ws).stdout or ""
        assert "leak" not in out.lower()
        assert "ENV=null" in out

    def test_ssh_key_path_unreadable_from_java(self):
        ws = tempfile.mkdtemp(prefix="eb_java_ws_")
        out = _compile_and_run(_java_main_src(), ws).stdout or ""
        assert "CRED_BLOCKED=" in out  # expanduser("~") is empty -> path invalid


class TestJavaFailClosed:
    def test_java_validator_maps_runner_failure_to_fail_closed(self, monkeypatch):
        from src import java_validator as jv

        def boom(*args, **kwargs):
            raise ExecutionBoundaryError("secure runner failed to start")

        monkeypatch.setattr(jv, "run_generated_code", boom)
        # A syntactically correct solution must still be reported as a failure when the
        # secure runner cannot execute it -- no silent pass / host fallback.
        result = jv.validate_java_solution(
            "public class Solution {\n public static int add(int a, int b) { return a + b; }\n}\n"
        )
        assert result.passed is False
        assert result.score == 0.0
        assert "execution boundary" in result.error.lower()
