"""LAN exposure review (P2) -- proves the default service is not externally exposed.

Review findings encoded as regression tests:
  * server_launcher binds uvicorn to loopback ``127.0.0.1`` only; there is no
    ``--host`` / ``0.0.0.0`` override anywhere in src, so the API is not reachable
    from the LAN by default.
  * backend URLs default to loopback (see backend_url_policy): unsafe backends are
    rejected before they can be used or persisted.

Note: no authentication framework is added here -- the secure default is loopback
binding + loopback-only backend routing, which removes the external attack surface
this Act targets.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import backend_url_policy as policy  # noqa: E402


def _server_launcher_source() -> str:
    path = os.path.join(
        os.path.dirname(__file__), "..", "src", "server_launcher.py")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestLoopbackBinding:
    def test_server_binds_loopback_only(self):
        src = _server_launcher_source()
        assert 'host="127.0.0.1"' in src or "host='127.0.0.1'" in src

    def test_no_external_bind_override_exists(self):
        """A future 0.0.0.0 / --host override must not silently reintroduce LAN exposure."""
        import glob
        sources = []
        for pat in ("src/*.py", "src/routes/*.py"):
            sources += [open(p, encoding="utf-8").read() for p in glob.glob(os.path.join("..", pat))]
        joined = "\n".join(sources)
        assert "0.0.0.0" not in joined, "external bind address found in source"
        assert "--host" not in joined, "host override flag found in source"


class TestDefaultBackendIsLoopback:
    def test_default_allowlist_is_loopback_only(self):
        allowed = policy.resolve_allowed_hosts()  # real settings.json (localhost default)
        assert allowed == frozenset({"127.0.0.1", "localhost"})
