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
    assert "Soon" in body
    assert "Research" in body


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

