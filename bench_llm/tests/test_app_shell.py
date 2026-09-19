"""Regression coverage for the persistent application-shell sidebar Act.

Proves that the left sidebar is now a single, persistent application shell shared
across every user-facing page (/, /results, /speed/results/{id}, /v2/results/{id}),
that only the right content pane changes when navigating, and that the benchmark
launcher on the default page is fully preserved.

Behaviour is asserted statically against served HTML + the shared app-shell.js
(the single source of truth), mirroring the static-asset approach used elsewhere in
the suite. Runtime active-state / navigation behaviour is covered by browser
acceptance, not here.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

import src.main  # noqa: E402,F401

_PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = _PROJECT_ROOT / "static"

client = TestClient(src.main.app)

# Every user-facing page that must carry the persistent shell.
SHELLED_PAGES = [
    ("/", "shared.css"),
    ("/results", "results-shell.css"),
    ("/speed/results/any-run-id", "speed-result.css"),
    ("/v2/results/any-run-id", "v2-result.css"),
]


# ---------------------------------------------------------------------------
# 1. The sidebar is present + shared on every page
# ---------------------------------------------------------------------------

def test_every_shellled_page_mounts_the_sidebar_once():
    for path, _ in SHELLED_PAGES:
        body = client.get(path).text
        assert body.count("app-sidebar-mount") == 1, f"{path}: expected exactly one mount"
        assert "/static/app-shell.js" in body, f"{path}: app-shell.js not loaded"


def test_no_page_duplicates_the_sidebar_markup():
    """The sidebar is injected once; no served HTML contains a second inline copy."""
    for path, _ in SHELLED_PAGES:
        body = client.get(path).text
        assert 'class="rs-sidebar"' not in body, f"{path}: duplicated inline sidebar"


def test_results_page_has_no_inline_sidebar_block():
    """results.html no longer ships its own <aside>; it uses the shared mount."""
    body = client.get("/results").text
    assert 'class="rs-sidebar"' not in body
    assert "app-sidebar-mount" in body


def test_app_shell_is_the_single_source_of_truth():
    js = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
    # Brand + both nav groups present.
    assert "Solo Dev LLM Bench" in js
    assert 'aria-label="Benchmarks"' in js
    assert 'aria-label="Results"' in js
    # All five dimensions defined once.
    for view in ("overall", "speed", "workflow", "context", "intelligence"):
        assert f'data-view="{view}"' in js


# ---------------------------------------------------------------------------
# 2. Active state reflects the current location (static default + URL logic)
# ---------------------------------------------------------------------------

def test_default_markup_overall_is_active():
    """By default (no JS) Overall is the active Results dimension."""
    js = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
    overall = _button(js, "overall")
    assert 'aria-current="page"' in overall
    assert "rs-nav-item-active" in overall


def test_active_location_logic_covers_all_routes():
    """The URL -> active-location mapping covers every route family."""
    js = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
    # The helper reads the path and maps each route family to an active location.
    assert 'location.pathname' in js
    # Off-page dimension navigation targets a hash tab on the Results shell.
    assert "/results#" in js
    # Speed / v2 detail routes are recognised by their path prefix.
    assert "speed/results" in js
    assert "v2/results" in js


# ---------------------------------------------------------------------------
# 3. Context / Intelligence remain honest (Coming soon / Research)
# ---------------------------------------------------------------------------

def test_context_is_coming_soon_and_disabled():
    js = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
    item = _button(js, "context")
    assert "Coming soon" in item
    assert "disabled" in item
    assert "rs-nav-item-disabled" in item


def test_intelligence_is_research_and_disabled():
    js = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
    item = _button(js, "intelligence")
    assert "Research" in item
    assert "disabled" in item
    assert "rs-nav-item-disabled" in item


# ---------------------------------------------------------------------------
# 4. Redundant navigation removed
# ---------------------------------------------------------------------------

def test_no_back_to_benchmarks_anywhere():
    for path, _ in SHELLED_PAGES:
        assert "Back to benchmarks" not in client.get(path).text
    assert "Back to benchmarks" not in (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")


def test_no_duplicate_primary_navigation():
    """Exactly one primary nav system exists per page (the persistent sidebar)."""
    for path, _ in SHELLED_PAGES:
        body = client.get(path).text
        assert "role=\"tablist\"" not in body
        assert "role=\"tab\"" not in body


# ---------------------------------------------------------------------------
# 5. Benchmark launcher functionality is preserved on the default page
# ---------------------------------------------------------------------------

def test_launcher_controls_present_on_default_page():
    body = client.get("/").text
    # Model state + selection.
    assert 'id="model-select"' in body
    assert 'id="refresh-models"' in body
    # Load/unload lifecycle control.
    assert 'id="load-unload-btn"' in body
    # Benchmark suite launchers.
    assert 'id="run-speed"' in body
    assert 'id="run-workflow"' in body


def test_launcher_scripts_loaded_on_default_page():
    body = client.get("/").text
    assert "/static/dashboard.js" in body
    assert "/static/dashboard-charts.js" in body


# ---------------------------------------------------------------------------
# 6. Existing result routes remain valid (deep links still resolve)
# ---------------------------------------------------------------------------

def test_all_result_routes_return_200():
    for path, _ in SHELLED_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_speed_detail_route_still_serves_speed_css():
    resp = client.get("/speed/results/any-run-id")
    assert "speed-result.css" in resp.text


def test_v2_detail_route_still_serves_v2_css():
    resp = client.get("/v2/results/any-run-id")
    assert "v2-result.css" in resp.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _button(js, view):
    pattern = re.compile(
        r'<button[^>]*data-view="%s".*?</button>' % re.escape(view),
        re.DOTALL,
    )
    m = pattern.search(js)
    assert m is not None, f"missing nav button for {view}"
    return m.group(0)
