"""Backend URL allow-list policy (SSRF boundary, P1).

The benchmark API must *not* act as a generic HTTP requester toward arbitrary
destinations.  LM Studio / backend URLs default to ``localhost`` and are validated
**before** they are persisted or used for any outbound request.

Default allow-list (secure):

    * ``127.0.0.1``
    * ``localhost``

Optionally-configured trusted addresses may be added via the ``trusted_backend_hosts``
key in ``config/settings.json`` (a list of hostnames / IP literals; scheme and port
are ignored for matching).  Anything else -- arbitrary public or private
destinations -- is **rejected by default**.  Malformed URLs are rejected as well.

The policy is allow-list based (no DNS resolution, no "block known-bad" list), so it
is deterministic and safe to run offline: only hosts explicitly named in the
allow-list can ever be contacted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlunparse, urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Secure default allow-list: loopback only.
DEFAULT_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: Only these URL schemes may be used to reach the backend.
ALLOWED_SCHEMES = frozenset({"http", "https"})

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"


class BackendUrlPolicyError(Exception):
    """Raised when a backend URL violates the allow-list policy."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_host(host: Optional[str]) -> str:
    """Lower-case and strip IPv6 brackets so allow-list matching is stable."""
    if not host:
        return ""
    host = host.strip().lower()
    if len(host) >= 2 and host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def is_allowed_host(host: Optional[str], allowed_hosts: Iterable[str]) -> bool:
    """Return ``True`` if *host* (name or IP) is in *allowed_hosts* (case-insensitive)."""
    normed = {_normalize_host(a) for a in (allowed_hosts or [])}
    return _normalize_host(host) in normed


def resolve_allowed_hosts(config_path: Path = _DEFAULT_CONFIG_PATH) -> frozenset[str]:
    """Return the effective allow-list.

    Starts from :data:`DEFAULT_ALLOWED_HOSTS` and merges any
    ``trusted_backend_hosts`` entries from ``settings.json``.  A missing or
    unreadable config falls back to the secure loopback-only default.
    """
    allowed = set(DEFAULT_ALLOWED_HOSTS)
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return frozenset(allowed)

    extra = cfg.get("trusted_backend_hosts") if isinstance(cfg, dict) else None
    if isinstance(extra, (list, tuple, set)):
        for item in extra:
            if isinstance(item, str) and item.strip():
                allowed.add(_normalize_host(item))
    return frozenset(allowed)


def validate_backend_url(
    url: Optional[str],
    allowed_hosts: Optional[Iterable[str]] = None,
) -> str:
    """Validate a backend URL against the allow-list.

    Args:
        url: The candidate LM Studio / backend base URL.
        allowed_hosts: Explicit allow-list override. Defaults to
            :data:`DEFAULT_ALLOWED_HOSTS` (loopback only).

    Returns:
        A normalized base URL string (scheme + host[:port], trailing slash removed),
        safe to append API endpoints to.

    Raises:
        BackendUrlPolicyError: if the URL is malformed, uses a disallowed scheme, or
            targets a host that is not in the allow-list.
    """
    if allowed_hosts is None:
        allowed_hosts = DEFAULT_ALLOWED_HOSTS

    if url is None or not isinstance(url, str) or not url.strip():
        raise BackendUrlPolicyError("missing backend URL")

    raw = url.strip().rstrip("/")

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise BackendUrlPolicyError(f"malformed backend URL {url!r}: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BackendUrlPolicyError(
            f"disallowed scheme {scheme or '<none>'!r} (only http/https permitted)"
        )

    netloc = parsed.netloc
    if not netloc:
        raise BackendUrlPolicyError(f"backend URL is missing a host: {url!r}")

    # Drop any embedded userinfo so credentials cannot smuggle an unexpected host.
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]

    host = netloc.split(":", 1)[0]  # keep port; we only allow-list the host component
    if not _normalize_host(host):
        raise BackendUrlPolicyError(f"backend URL is missing a host: {url!r}")

    if not is_allowed_host(host, allowed_hosts):
        raise BackendUrlPolicyError(
            f"backend host {host!r} is not in the allow-list (loopback-only by default); "
            f"add it to 'trusted_backend_hosts' in config to permit it"
        )

    return urlunparse((scheme, netloc, "", "", "", ""))
