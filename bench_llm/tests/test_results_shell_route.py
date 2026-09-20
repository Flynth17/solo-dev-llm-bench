"""HTTP + static-asset tests for the unified Results UI foundation (RM-26-AA-0016).

Covers the shared Results shell: page load, navigation structure / status chips,
the authoritative ranking read model contract (composite unavailable, N/A never
coerced to zero), and that the delivered single-run result routes still respond.

Frontend behaviour is asserted statically against the shipped JS/CSS (mirroring
test_smoke.py's approach for static assets) rather than via a browser: the shell
performs no benchmark scoring, invents no composite, renders missing values as an
em-dash (never zero), and keeps N/A / unsupported / failure distinct.

The persistent application-shell sidebar is defined ONCE in app-shell.js (the single
source of truth) and injected client-side into every page's #app-sidebar-mount; the
assertions below therefore check that shared definition rather than duplicated markup
in each served HTML file.
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

# The single source of truth for the persistent sidebar.
APP_JS = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")


def _served(path):
    return client.get(path).text


def _nav_items(js):
    """Return every <button>...</button> nav item in a JS source string."""
    pattern = re.compile(r'<button[^>]*data-view=".*?".*?</button>', re.DOTALL)
    return pattern.findall(js)


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
    # The shared sidebar is injected by app-shell.js (single source of truth).
    assert "/static/app-shell.js" in body
    assert "app-sidebar-mount" in body


def test_all_pages_mount_the_shared_sidebar():
    """Every user-facing page mounts the same single sidebar and loads app-shell.js."""
    for path in ("/", "/results", "/speed/results/any-run-id", "/v2/results/any-run-id"):
        body = _served(path)
        assert body.count("app-sidebar-mount") == 1, f"{path} missing/dupe mount"
        assert "/static/app-shell.js" in body, f"{path} does not load app-shell.js"


def test_results_shell_has_all_five_view_containers():
    """The five view containers are static content on the Results page."""
    body = client.get("/results").text
    for view in ("overall", "speed", "workflow", "context", "intelligence"):
        assert f"id=\"view-{view}\"" in body, f"missing view container: {view}"


# ---------------------------------------------------------------------------
# 1b. Persistent left sidebar navigation (single source of truth = app-shell.js)
# ---------------------------------------------------------------------------

def test_sidebar_source_defines_all_five_dimensions():
    js = APP_JS
    for view in ("overall", "speed", "workflow", "context", "intelligence"):
        assert f'data-view="{view}"' in js, f"missing nav item: {view}"
        assert f"id=\"nav-{view}\"" in js, f"missing nav id: {view}"


def test_navigation_status_chips_reflect_reality():
    """Delivered areas are marked delivered; planned/research are not functional."""
    js = APP_JS
    # Speed + Workflow are delivered.
    assert 'data-view="speed"' in js
    assert 'data-view="workflow"' in js
    # Overall must NOT claim a composite score.
    assert "No composite" in js
    # Context (RM-26-AA-0018) is now delivered; Intelligence is research.
    assert "Delivered" in js
    assert "Research" in js


def test_results_page_uses_left_sidebar_not_top_tabs():
    """Primary dimension nav is a persistent left sidebar, not a top tab bar.

    The sidebar markup lives only in app-shell.js (injected), so no served HTML
    contains duplicated sidebar markup or the old tab semantics.
    """
    for path in ("/", "/results", "/speed/results/any-run-id", "/v2/results/any-run-id"):
        body = _served(path)
        # No duplicated inline sidebar in any page (it is injected once).
        assert 'class="rs-sidebar"' not in body, f"{path} duplicates the sidebar"
        # Old top-level tab semantics are gone everywhere (no duplicate navigation).
        assert "role=\"tablist\"" not in body, f"{path} still has a tablist"
        assert "role=\"tab\"" not in body, f"{path} still has tab roles"
        # \brs-tab\b uses a word boundary so the shared .rs-table primitive (used by
        # the Speed benchmark table) does not false-match this old top-nav guard.
        assert not re.search(r"\brs-tab\b", body), f"{path} still references the old rs-tab nav class"


def test_redundant_back_to_benchmarks_removed():
    """The standalone escape hatch is gone; the sidebar Benchmarks entry is home."""
    for path in ("/", "/results", "/speed/results/any-run-id", "/v2/results/any-run-id"):
        assert "Back to benchmarks" not in _served(path), f"{path} still has back link"
    assert "Back to benchmarks" not in APP_JS


def test_sidebar_nav_is_semantic_landmarked():
    js = APP_JS
    # Semantic <nav> with an accessible label (not a bare list of buttons).
    assert '<nav class="rs-nav" aria-label="Results">' in js
    # Live items are real buttons, not fake links.
    assert 'class="rs-nav-item' in js
    assert 'href="#"' not in js


def test_sidebar_live_dimensions_are_interactive_buttons():
    """Overall / Speed / Workflow are keyboard-reachable, enabled buttons."""
    for item in _nav_items(APP_JS):
        view = re.search(r'data-view="([^"]+)"', item).group(1)
        if view in ("overall", "speed", "workflow"):
            assert "disabled" not in item.split(">")[0], f"{view} should be enabled"
            assert "rs-nav-item-disabled" not in item


def test_sidebar_context_is_delivered_and_enabled():
    """Context is delivered: shown, marked delivered, and interactive."""
    item = next(i for i in _nav_items(APP_JS) if 'data-view="context"' in i)
    assert item is not None
    # Delivered status chip (RM-26-AA-0018).
    assert "Delivered" in item
    assert "rs-chip-delivered" in item
    # Enabled: a real, interactive button — no disabled state.
    assert 'disabled' not in item
    assert "rs-nav-item-disabled" not in item
    assert "aria-disabled=\"true\"" not in item


def test_sidebar_intelligence_is_disabled_and_research():
    """Intelligence is research: shown, disabled, non-clickable."""
    item = next(i for i in _nav_items(APP_JS) if 'data-view="intelligence"' in i)
    assert item is not None
    assert 'disabled' in item
    assert "rs-nav-item-disabled" in item
    assert "Research" in item


def test_sidebar_active_state_uses_semantic_current():
    """The canonical default (Overall) exposes active state via aria-current."""
    overall = next(i for i in _nav_items(APP_JS) if 'data-view="overall"' in i)
    assert overall is not None
    assert 'aria-current="page"' in overall
    assert "rs-nav-item-active" in overall
    # Only one live item may carry the active marker by default.
    items = _nav_items(APP_JS)
    assert sum(i.count('aria-current="page"') for i in items) == 1


def test_sidebar_has_a_benchmarks_home_entry():
    """The persistent sidebar gains a Benchmarks / Run home entry (single nav)."""
    assert 'href="/"' in APP_JS
    assert "Benchmarks / Run" in APP_JS


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


def test_app_shell_js_performs_no_benchmark_logic():
    """The shared shell only builds nav + active state; it never touches benchmark/API logic."""
    assert "/api/ranking" not in APP_JS
    assert "run_benchmark" not in APP_JS
    assert "loadModel" not in APP_JS and "unloadModel" not in APP_JS


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


def test_index_page_still_loads_launcher_scripts():
    """The benchmark launcher (dashboard.js) is still wired on the default page."""
    body = client.get("/").text
    assert "/static/dashboard.js" in body
    assert "/static/dashboard-charts.js" in body


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
# 5b. Overall view correction (RM-26-AA-0016): no partial leaderboard
# ---------------------------------------------------------------------------

def test_overall_view_suppresses_partial_leaderboard_but_keeps_no_composite():
    """Overall reads as deliberately unavailable, not partially implemented.

    While no approved composite exists the Overall view must SUPPRESS the model-by-model
    cards / configuration rows / evidence counts (the partial leaderboard) while KEEPING
    the explicit NO COMPOSITE badge, the explanatory banner and an authoritative
    per-dimension availability list. The L0->L1 collapsible primitive is retained as a
    foundation component but must not be invoked on this view.
    """
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")

    # Suppressed: the composite-unavailable Overall view no longer invokes the L0
    # model/config/evidence-count leaderboard rendering.
    assert "renderL0Models(ranking.models)" not in js, (
        "Overall must not render the L0 model/config partial leaderboard"
    )

    # Retained as a foundation primitive (not invoked here): definition stays present.
    assert "function renderL0Models(models)" in js

    # Kept: explicit NO COMPOSITE badge + explanatory banner title.
    assert "NO COMPOSITE" in js
    assert "Composite score unavailable" in js

    # Kept: authoritative per-dimension availability, rendered with ✓/○ markers and the
    # backend-provided reason (single source of truth -- no frontend recomputation).
    assert "Available dimensions" in js
    assert "available_dimensions" in js
    assert "dimension_reasons" in js
    assert "\\u2713" in js  # ✓ check marker for approved families
    assert "\\u25CB" in js  # ○ circle marker for unimplemented families


def test_workflow_incomplete_runs_are_inspectable_diagnostic_not_a_score():
    """Incomplete-but-valid Workflow runs surface as inspectable diagnostic evidence.

    The Results UI must NOT emit empty rows for incomplete runs (the old partial-
    leaderboard behaviour) and must NOT claim an approved fraction for them. Instead,
    real check counts + per-suite breakdown are surfaced with an explicit incomplete /
    diagnostic marker, while N/A stays distinct from a canonical score.
    """
    js = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")

    # Diagnostic path exists: reads backend-exposed agentic_diagnostic_runs and marks
    # entries as diagnostic so they render distinctly from canonical results.
    assert "agentic_diagnostic_runs" in js
    assert "diagnostic:" in js

    # Explicit incomplete / diagnostic indicator is rendered (never a percentage claim).
    assert "inspectable diagnostic evidence" in js

    # The old empty-row pattern is gone: eligible runs are only emitted when they carry
    # an authoritative component score; ineligible runs are handled via the diagnostic path.
    assert "if (!ag) { return; }" in js


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
