"""Backend URL allow-list policy regression tests (P1 SSRF boundary).

Covers: 127.0.0.1 allowed, localhost allowed, explicitly configured trusted address
allowed when configured, arbitrary public/private destinations rejected by default,
malformed URLs rejected, and end-to-end rejection through the live /api/config route.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import backend_url_policy as policy  # noqa: E402
from src.backend_url_policy import BackendUrlPolicyError, validate_backend_url  # noqa: E402


class TestAllowListDefault:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:1234",
        "https://localhost:1234/",
        "http://LOCALHOST/v1/models",          # case-insensitive host
        "https://127.0.0.1",
    ])
    def test_loopback_allowed(self, url):
        out = validate_backend_url(url)
        assert out.startswith(("http://", "https://"))

    @pytest.mark.parametrize("url", [
        "http://0.0.0.0:1234",         # not loopback-allowlisted
        "http://example.com",          # arbitrary public destination
        "https://192.168.1.50:1234",   # arbitrary private destination
        "https://10.0.0.5",            # arbitrary private destination
        "ftp://localhost/x",           # disallowed scheme
        "http://",                     # missing host
        "",                            # empty
        None,                          # missing
    ])
    def test_non_loopback_rejected(self, url):
        with pytest.raises(BackendUrlPolicyError):
            validate_backend_url(url)


class TestConfiguredTrustedHosts:
    def test_trusted_host_allowed_only_when_configured(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(
            '{"lm_studio_url": "http://localhost:1234", '
            '"trusted_backend_hosts": ["benchmark.internal"]}',
            encoding="utf-8",
        )
        allowed = policy.resolve_allowed_hosts(tmp_path / "does-not-exist.json")
        assert allowed == frozenset({"127.0.0.1", "localhost"})  # secure default when no config

        allowed = policy.resolve_allowed_hosts(settings)
        assert "benchmark.internal" in allowed and "127.0.0.1" in allowed

        out = validate_backend_url("http://benchmark.internal:9000/x", allowed)
        assert out == "http://benchmark.internal:9000"
        # A different public host is still rejected even with the trusted set present.
        with pytest.raises(BackendUrlPolicyError):
            validate_backend_url("http://evil.example.com", allowed)

    def test_unreadable_config_falls_back_to_secure_default(self, tmp_path):
        missing = tmp_path / "nope.json"
        assert not missing.exists()
        allowed = policy.resolve_allowed_hosts(missing)
        assert allowed == frozenset({"127.0.0.1", "localhost"})


class TestConfigRouteEndToEnd:
    """The live POST /api/config must reject an unsafe backend before persisting it."""

    def _client(self):
        from starlette.testclient import TestClient
        from src.main import app  # noqa: F401  (registers all routes)
        return TestClient(app)

    def test_config_route_rejects_non_loopback_and_accepts_loopback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.config_loader._DEFAULT_CONFIG_PATH", str(tmp_path / "settings.json"))
        (tmp_path / "settings.json").write_text(
            '{"lm_studio_url": "http://localhost:1234"}', encoding="utf-8")
        client = self._client()

        # Safe localhost update persists.
        r = client.post("/api/config", json={"lm_studio_url": "http://localhost:1234"})
        assert r.status_code == 200, r.text

        # Unsafe backend is rejected with 400 and NOT persisted.
        r = client.post("/api/config", json={"lm_studio_url": "http://evil.example.com:1234"})
        assert r.status_code == 400, r.text
        got = client.get("/api/config").json()
        assert got["lm_studio_url"] == "http://localhost:1234"
