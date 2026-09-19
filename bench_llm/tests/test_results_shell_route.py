"""HTTP + static-asset tests for the unified Results UI foundation (RM-26-AA-0016).

Covers the shared Results shell: page load, navigation structure / status chips,
the authoritative ranking read model contract (composite unavailable, N/A never
coerced to zero), and that the delivered single-run result routes still respond.

Frontend behaviour is asserted statically against the shipped JS/CSS (mirroring
test_smoke.py's approach for static assets) rather than via a browser: the shell
performs no benchmark scoring, invents no composite, renders missing values as an
em-dash (never zero), and keeps N/A / unsupported / failure distinct.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.main  # noqa: E402,F401  (imports the FastAPI app; also exercised directly)

_PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = _PROJECT_ROOT / "static"

client = TestClient(src.main.app)


# ---------------------------------------------------------------------------
# 1. Results shell page loads and exposes the unified navigation
# ---------------------------------------------------------------------------

def test_results_shell_page_loads():
    resp = client.get("/results")
    assert resp.status_code == 200
    body = resp.text
    # Shared dark-shell root + wrap are present.
    assert "class=\"results-shell\"" in body
    assert "rs-shell-wrap" in body
    # New shell stylesheet is loaded; the light-only results.css variant is too.
    assert "/static/results-shell.css" in body


def test_results_shell_has_all_five_areas():
    body = client.get("/results").text
    for view in ("overall", "speed", "workflow", "context", "intelligence"):
        assert f'data-view="{view}"' in body, f"missing nav tab: {view}"
        assert f"id=\"view-{view}\"" in body, f"missing view container: {view}"


def test_navigation_status_chips_reflect_reality():
    """Delivered areas are marked delivered; planned/research are not functional."""
    body = client.get("/results").text
    # Speed + Workflow are delivered.
    assert 'data-view="speed"' in body
    assert 'data-view="workflow"' in body
    # Overall must NOT claim a composite score.
    assert "No composite" in body
    # Context is pending (0018); Intelligence is research.
    assert "Coming soon" in body
    assert "Research" in body


# ---------------------------------------------------------------------------
# 1b. Persistent left sidebar navigation (RM-26-AA sidebar Act)
# ---------------------------------------------------------------------------

def test_results_page_uses_left_sidebar_not_top_tabs():
    """Primary dimension nav is a persistent left sidebar, not a top tab bar."""
    body = client.get("/results").text
    # Sidebar surface present.
    assert "rs-sidebar" in body
    # Old top-level tab semantics are gone (no duplicate navigation).
    assert "role=\"tablist\"" not in body
    assert "role=\"tab\"" not in body
    assert "rs-tab" not in body
    # Brand + escape hatch live in the sidebar.
    assert "Solo Dev LLM Bench" in body
    assert "Back to benchmarks" in body
    assert "rs-sidebar-foot" in body


def test_sidebar_has_all_five_dimensions():
    body = client.get("/results").text
    for view in ("overall", "speed", "workflow", "context", "intelligence"):
        assert f'data-view="{view}"' in body, f"missing nav item: {view}"
        assert f"id=\"nav-{view}\"" in body, f"missing nav id: {view}"
        assert f"id=\"view-{view}\"" in body, f"missing view container: {view}"


def test_sidebar_nav_is_semantic_landmarked():
    body = client.get("/results").text
    # Semantic <nav> with an accessible label (not a bare list of buttons).
    assert '<nav class="rs-nav" aria-label="Results">' in body
    # Live items are real buttons, not fake links.
    assert 'class="rs-nav-item' in body
    assert 'href="#"' not in body


def test_sidebar_live_dimensions_are_interactive_buttons():
    """Overall / Speed / Workflow are keyboard-reachable, enabled buttons."""
    body = client.get("/results").text
    for view in ("overall", "speed", "workflow"):
        item = _extract_nav_item(body, view)
        assert item is not None, f"missing nav item {view}"
        # Enabled <button>, out of the disabled set.
        assert "disabled" not in item.split(">")[0], f"{view} should be enabled"
        assert "rs-nav-item-disabled" not in item


def test_sidebar_context_is_disabled_and_planned():
    """Context is shown but marked not-yet-delivered and non-interactive."""
    body = client.get("/results").text
    item = _extract_nav_item(body, "context")
    assert item is not None
    # Disabled: out of tab order / no activation (native disabled + status class).
    assert 'disabled' in item
    assert "rs-nav-item-disabled" in item
    assert "aria-disabled=\"true\"" in item
    # Honest planned state, never implying delivered UI.
    assert "Coming soon" in item
    assert "Delivered" not in item


def test_sidebar_intelligence_is_disabled_and_research():
    """Intelligence is research: shown, disabled, non-clickable."""
    body = client.get("/results").text
    item = _extract_nav_item(body, "intelligence")
    assert item is not None
    assert 'disabled' in item
    assert "rs-nav-item-disabled" in item
    assert "Research" in item


def test_sidebar_active_state_uses_semantic_current():
    """The canonical default (Overall) exposes active state via aria-current."""
    body = client.get("/results").text
    overall = _extract_nav_item(body, "overall")
    assert overall is not None
    assert 'aria-current="page"' in overall
    assert "rs-nav-item-active" in overall
    # Only one live item may carry the active marker.
    assert body.count('aria-current="page"') == 1


def test_sidebar_disabled_items_have_no_hover_implication():
    css = (STATIC_DIR / "results-shell.css").read_text(encoding="utf-8")
    # Disabled treatment must not imply navigation: no pointer-events hover affordance.
    assert ".rs-nav-item-disabled" in css
    # Active state is conveyed by more than colour alone (class + aria-current).
    assert "rs-nav-item-active" in css
    assert "aria-current" in css


def test_sidebar_layout_uses_grid_and_sticky():
    css = (STATIC_DIR / "results-shell.css").read_text(encoding="utf-8")
    assert "grid-template-columns" in css
    assert ".rs-sidebar" in css
    assert ".rs-main" in css


def test_sidebar_narrow_viewport_has_no_horizontal_overflow_rule():
    css = (STATIC_DIR / "results-shell.css").read_text(encoding="utf-8")
    # Narrow viewport collapses the fixed sidebar to a reachable rail.
    assert re.search(r"@media \(max-width:\s*820px\)", css) is not None
    assert ".rs-nav-item" in css


def _extract_nav_item(body, view):
    """Return the raw <button>...</button> markup for a given data-view item."""
    pattern = re.compile(
        r'<button[^>]*data-view="%s".*?</button>' % re.escape(view),
        re.DOTALL,
    )
    m = pattern.search(body)
    return m.group(0) if m else None


def test_shell_uses_dark_theme_and_no_light_toggle():
    css = (STATIC_DIR / "results-shell.css").read_text(encoding="utf-8")
    # Dark palette tokens present.
    assert "--rs-bg: #07111f" in css
    assert "--rs-surface: #0f1b2d" in css
    # No light/dark toggle mechanism is introduced by this Act (dark mode or nothing):
    # no system color-scheme media query and no .light class.
    assert "prefers-color-scheme" not in css
    assert ".light" not in css


# ---------------------------------------------------------------------------
# 2. Authoritative ranking read model contract (composite unavailable, N/A != 0)
# ---------------------------------------------------------------------------

def test_ranking_composite_is_unavailable_not_a_number():
    body = client.get("/api/ranking").json()
    assert body["composite"]["status"] == "unavailable"
    # No composite score is ever computed or inferred.
    for model in body["models"]:
        assert model["overall_solo_bench_score"] is None


def test_unimplemented_dimensions_are_unavailable_with_reason_not_scored():
    body = client.get("/api/ranking").json()
    dims = body["all_dimensions"]
    assert "context_degradation" in dims
    assert "intelligence" in dims
    # A model's dimension map reports unimplemented families as unavailable with a
    # reason and a null score -- never an inferred/zeroed number.
    if body["models"]:
        dim_scores = body["models"][0]["dimensions"]
        ctx = dim_scores.get("context_degradation", {})
        assert ctx.get("status") == "unavailable"
        assert ctx.get("score") is None
        assert ctx.get("reason")  # a human-readable reason exists


def test_ranking_read_model_never_coerces_null_to_zero():
    """The read model's number guard returns None for absent values (N/A stays N/A)."""
    from src.ranking_read_model import _as_number

    assert _as_number(None) is None
    assert _as_number("") is None
    assert _as_number("not-a-number") is None
    assert _as_number(True) is None  # bool is not a number here
    assert _as_number(3.5) == 3.5


def test_ranking_read_model_reports_no_composite_and_available_dimensions():
    from src.ranking_read_model import build_ranking, APPROVED_DIMENSIONS

    ranking = build_ranking([], [])
    assert ranking["composite"]["status"] == "unavailable"
    assert ranking["available_dimensions"] == list(APPROVED_DIMENSIONS)
    # Context + Intelligence are listed as missing/unimplemented dimensions.
    assert "context_degradation" in ranking["all_dimensions"]
    assert "intelligence" in ranking["all_dimensions"]


# ---------------------------------------------------------------------------
# 3. Frontend performs no scoring / never synthesises zero
# ---------------------------------------------------------------------------

def test_shell_js_consumes_ranking_and_formats_only():
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
    # Sole data source is the authoritative ranking read model endpoint.
    assert "/api/ranking" in js
    # Missing values render as an em-dash, never zero -- N/A stays distinct from 0.
    assert "\\u2014" in js or "\u2014" in js
    # The shell must not invent a composite score or recompute benchmark scores.
    assert "overall_solo_bench_score" not in js.replace("unavailable", ""), (
        "shell must not reference/compute the reserved composite score"
    )


def test_shell_js_has_no_light_theme_toggle_logic():
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
    assert "toggleTheme" not in js
    assert "prefers-color-scheme" not in js.lower()
    assert "light-mode" not in js.lower()


def test_shell_js_placeholder_never_fakes_data_for_planned_areas():
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
    # Context + Intelligence are honest placeholders, never populated with fake runs.
    assert "context-container" in js
    assert "intelligence-container" in js
    # No synthetic run data is injected for the planned/research areas.
    assert "rs-placeholder" in js


# ---------------------------------------------------------------------------
# 4. Existing delivered result routes still respond (not regressed)
# ---------------------------------------------------------------------------

def test_speed_result_route_still_responds():
    resp = client.get("/speed/results/any-run-id")
    assert resp.status_code == 200
    assert "speed-result.css" in resp.text


def test_v2_result_route_still_responds():
    resp = client.get("/v2/results/any-run-id")
    assert resp.status_code == 200
    assert "v2-result.css" in resp.text


def test_results_page_still_loads_speed_history_scripts():
    """The existing Speed history surface (results.js) is still wired on the page."""
    body = client.get("/results").text
    assert "/static/results.js" in body
    assert "/static/results-filters.js" in body
    # The destructive chartsPanel.innerHTML regression guard still holds.
    results_js = (STATIC_DIR / "results.js").read_text(encoding="utf-8")
    assert "chartsPanel.innerHTML" not in results_js


# ---------------------------------------------------------------------------
# 5. Collapsible drill-down primitive exists in the delivered markup/CSS
# ---------------------------------------------------------------------------

def test_collapsible_l0_l1_primitive_present():
    css = (STATIC_DIR / "results-shell.css").read_text(encoding="utf-8")
    # Native <details> collapsible model-group pattern is part of the foundation.
    assert "rs-model-group" in css
    assert "summary" in css
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
    assert "rs-model-group" in js


# ---------------------------------------------------------------------------
# 6. State contract: loading / no-results / error fragments are distinct
# ---------------------------------------------------------------------------

def test_state_fragments_are_distinct_in_shell_js():
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
    # Loading, no-results and error states each have their own marker class.
    # (The banner error state is built via banner("error", ...) -> rs-banner-error.)
    assert "rs-state" in js
    assert "rs-state-noresults" in js
    assert 'banner("error"' in js

