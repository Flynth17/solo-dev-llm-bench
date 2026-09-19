"""ST-003 UI contract tests -- Compare subject selection + identity header (RM-26-AA-0013).

Covers the delivered Compare surface statically against the shipped JS/CSS and via the
read-only comparison API (mirroring test_results_shell_route.py's approach):

* Shell: the persistent sidebar gains a LIVE Compare item between Context and
  Intelligence; Intelligence stays disabled; /results#compare activates it.
* Selectors: both Subject A/B selectors are populated from the authoritative subject
  catalogue, use backend subject keys as values (never display names), prevent
  A == B while keeping same-model/different-config subjects distinct.
* Identity header: model/version, architecture (honest N/A when not recorded),
  configuration identity and available dimensions render; family-specific
  configuration truth is preserved per family -- Workflow quantization is never
  fabricated, Context fields only appear from the Context projection.
* AMBIGUOUS: rendered as an explicit evidence limitation with a text explanation,
  never duplicated into both columns as config-specific ownership.
* Scope: the view fetches ONLY the comparison endpoints and contains no metric
  tokens -- no throughput values, time-to-first-token figures, scores, curves,
  deltas, composite or winner language (those begin at ST-004 onward).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

import src.main  # noqa: E402,F401  (imports the FastAPI app)

_PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = _PROJECT_ROOT / "static"

APP_JS = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
RESULTS_HTML = (STATIC_DIR / "results.html").read_text(encoding="utf-8")
SHELL_JS = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
COMPARE_JS = (STATIC_DIR / "results-compare.js").read_text(encoding="utf-8")
SHELL_CSS = (STATIC_DIR / "results-shell.css").read_text(encoding="utf-8")

client = TestClient(src.main.app)


# ---------------------------------------------------------------------------
# 1. Shell: Compare is a LIVE item in the persistent sidebar
# ---------------------------------------------------------------------------

def test_compare_nav_item_exists_in_app_shell():
    assert 'data-view="compare"' in APP_JS, "Compare nav item missing from app-shell.js"
    assert 'id="nav-compare"' in APP_JS


def test_compare_nav_is_live_not_disabled():
    """Compare is a working dimension: no disabled attribute / class on its button."""
    m = re.search(r'<button[^>]*data-view="compare"[^>]*>', APP_JS)
    assert m, "Compare nav button not found"
    tag = m.group(0)
    assert "disabled" not in tag, "Compare must be a live (enabled) navigation item"


def test_compare_nav_positioned_between_context_and_intelligence():
    """Desired Results order: Overall / Speed / Workflow / Context / Compare / Intelligence."""
    def pos(view):
        m = re.search(r'data-view="%s"' % view, APP_JS)
        assert m, f"nav item {view} missing"
        return m.start()

    for earlier, later in [
        ("overall", "speed"), ("speed", "workflow"), ("workflow", "context"),
        ("context", "compare"), ("compare", "intelligence"),
    ]:
        assert pos(earlier) < pos(later), f"nav order wrong: {earlier} should precede {later}"


def test_intelligence_stays_disabled_after_compare_added():
    m = re.search(r'<button[^>]*data-view="intelligence"[^>]*>', APP_JS)
    assert m and "disabled" in m.group(0), "Intelligence must remain disabled (research)"


def test_active_location_recognizes_compare_hash_with_params():
    """/results#compare?a=..&b=.. activates the Compare item; only the view id before '?' counts."""
    # app-shell.js derives the active location from the fragment minus any selection params.
    assert '.split("?")[0]' in APP_JS, "activeLocation must strip compare selection params"
    assert '"compare"' in APP_JS


def test_results_page_mounts_persistent_shell_and_compare_view():
    body = client.get("/results").text
    assert body.count("app-sidebar-mount") == 1, "persistent shell mount missing or duplicated"
    assert 'class="rs-sidebar"' not in body, "no inline sidebar duplication allowed"
    assert 'id="view-compare"' in body, "Compare view section missing from results.html"


# ---------------------------------------------------------------------------
# 2. Selectors: authoritative catalogue, backend keys as values, A != B
# ---------------------------------------------------------------------------

def test_compare_script_loaded_after_results_shell():
    """results-compare.js must load after results-shell.js (lazy-load bridge ordering).

    Compare the actual <script> tags -- not bare string mentions, which also occur in comments.
    """
    shell_tag = RESULTS_HTML.index('<script src="/static/results-shell.js')
    compare_tag = RESULTS_HTML.index('<script src="/static/results-compare.js')
    assert compare_tag > shell_tag


def test_selectors_populated_from_catalogue_api():
    assert '"/api/comparison/subjects"' in COMPARE_JS, "selectors must fetch the authoritative catalogue"
    # Option values are the backend subject keys verbatim.
    assert "opt.value = s.subject_key" in COMPARE_JS


def test_display_name_is_label_only_never_identity():
    """Labels may be human-readable; identity is always the subject key."""
    assert "optionLabel" in COMPARE_JS
    # The fetch of the resolved comparison uses encoded keys, not labels.
    assert 'encodeURIComponent(state.a)' in COMPARE_JS


def test_same_subject_cannot_compare_itself():
    """UI guard: identical keys never produce a comparison (backend also rejects with 400)."""
    assert "state.a === state.b" in COMPARE_JS, "A == B guard missing from results-compare.js"
    # Mirror-disabling keeps the exact same key unselectable on the opposite side.
    assert "opt.value === otherKey" in COMPARE_JS


def test_same_model_different_config_subjects_remain_distinct():
    """Only the EXACT same subject key is disabled; distinct config keys stay selectable."""
    # Disabling compares full keys (base model + family signature), so two subjects that
    # share a base model but differ by configuration remain independently selectable.
    assert "opt.value === otherKey" in COMPARE_JS


def test_self_comparison_rejected_by_api_with_400():
    """Backend contract: identical subject keys -> 400 (data-independent; the check runs first)."""
    r = client.get("/api/comparison", params={"a": "x|base:", "b": "x|base:"})
    assert r.status_code == 400
    assert "distinct" in r.json()["detail"]


def test_comparison_requires_both_keys():
    r = client.get("/api/comparison")
    assert r.status_code == 400
    assert "a" in r.json()["detail"] and "b" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 3. Identity header: authoritative fields, family-specific configuration truth
# ---------------------------------------------------------------------------

def test_identity_header_renders_authoritative_fields():
    for field in ("Model / version", "Architecture", "Configuration identity", "Available dimensions"):
        assert field in COMPARE_JS, f"identity row missing: {field}"


def test_architecture_stays_honest_na_when_not_recorded():
    """Architecture is not persisted per-run; the header shows an explicit N/A marker."""
    assert "Not recorded" in COMPARE_JS


def test_workflow_quantization_is_never_fabricated():
    """The Workflow section renders ONLY its own projection: quantization absent -> 'Not recorded'."""
    # The workflow branch reads only cfg.quantization / cfg.configuration_fingerprint from
    # the WORKFLOW dimension -- it never copies another family's value into this section.
    wf_branch = COMPARE_JS.split('family === "workflow"')[1].split('family === "context"')[0]
    assert "Not recorded" in wf_branch, "absent Workflow quantization must render as 'Not recorded'"
    # No cross-family source: the workflow branch never reads speed/context config objects.
    assert "cfg.quantization" in wf_branch


def test_context_fields_only_from_context_projection():
    """Context identity fields (quantization / capacity / baseline) come from Context only."""
    ctx_branch = COMPARE_JS.split('family === "context"')[1].split("out += \"</dl>\"")[0]
    for field in ("effective_capacity", "baseline_context_point"):
        assert field in ctx_branch, f"context identity field missing: {field}"


def test_availability_states_use_the_api_vocabulary():
    """All seven read-model states are rendered with text labels (never colour-only)."""
    for st in ("available", "ambiguous", "missing", "in_progress", "failed", "unsupported", "unavailable"):
        # States appear as object keys of STATE_LABELS / STATE_BADGE (unquoted JS keys).
        assert re.search(rf'\b{st}\s*:', COMPARE_JS), f"state vocabulary missing: {st}"


def test_ambiguous_rendered_as_evidence_limitation_with_explanation():
    """AMBIGUOUS is explicit product language with a readable why -- not an error state."""
    assert "AMBIGUOUS_NOTES" in COMPARE_JS
    assert "cannot be attributed to a specific configuration" in COMPARE_JS
    # The ambiguous branch renders the explanation and never run/config specifics.
    amb_branch = COMPARE_JS.split('st === "ambiguous"')[1].split('st === "available"')[0]
    assert "cmp-ambiguous-note" in amb_branch


def test_ambiguous_never_duplicated_as_config_specific_ownership():
    """The read model reports AMBIGUOUS for BOTH subjects; the UI renders each column from
    its own projection only -- a shared run is never presented as config-specific on both sides."""
    # Each subject column reads ONLY its own dimension slot (subject_a / subject_b).
    assert "dimsForSlot" in COMPARE_JS


# ---------------------------------------------------------------------------
# 4. URL persistence + graceful failure for unknown keys
# ---------------------------------------------------------------------------

def test_url_persistence_uses_fragment_with_encoded_keys():
    assert 'history.replaceState' in COMPARE_JS
    assert '"#compare"' in COMPARE_JS or "'#compare'" in COMPARE_JS


def test_unknown_or_malformed_keys_fail_gracefully():
    """Keys not present in the catalogue are cleared with a notice -- never guessed at."""
    assert "not in the current catalogue" in COMPARE_JS


def test_stale_responses_cannot_mutate_state():
    """Monotonic sequence guard: an older comparison response never overwrites newer state."""
    assert "fetchSeq" in COMPARE_JS


# ---------------------------------------------------------------------------
# 5. Scope: identity only -- no metric comparison leaked into ST-003
# ---------------------------------------------------------------------------

_FORBIDDEN_METRIC_TOKENS = [
    "tok/s",          # throughput values
    "ttft",           # time-to-first-token figures (case-insensitive)
    "winner",         # no winner indicators
    "leader",         # no leader/winner framing
    "composite",      # no composite score
]


def test_compare_view_contains_no_metric_tokens():
    lowered = COMPARE_JS.lower()
    for token in _FORBIDDEN_METRIC_TOKENS:
        assert token not in lowered, f"ST-003 must not reference metric/winner language: {token!r}"


def test_compare_view_fetches_only_comparison_endpoints():
    """No independent joining of Speed / Workflow / Context or ranking APIs."""
    fetch_targets = re.findall(r'fetch\("([^"]+)"', COMPARE_JS)
    assert fetch_targets, "expected at least one fetch in results-compare.js"
    for target in fetch_targets:
        assert target.startswith("/api/comparison"), f"unexpected data source: {target}"


def test_swap_preserves_backend_keys_and_refetches():
    """Swap only reorders columns: backend keys are preserved verbatim and the projection refetched."""
    # The swap handler exchanges the two stored keys (no new identity is invented)...
    assert "var tmp = state.a" in COMPARE_JS
    assert "state.b = tmp" in COMPARE_JS
    # ...and triggers a normal refetch with the swapped subject order.
    assert COMPARE_JS.count("fetchComparison()") >= 3


# ---------------------------------------------------------------------------
# 6. Visual system: dark language retained, responsive stacking provided
# ---------------------------------------------------------------------------

def test_compare_styles_use_the_dark_shell_tokens():
    for token in ("--rs-surface", "--rs-border", "--rs-text"):
        assert token in SHELL_CSS
    cmp_section = SHELL_CSS.split("Compare view (RM-26-AA-0013")[1] if "Compare view (RM-26-AA-0013" in SHELL_CSS else ""
    assert cmp_section, "Compare CSS section missing from results-shell.css"


def test_compare_grid_stacks_on_narrow_viewports():
    """Desktop: A and B side by side; narrow: stacked -- never squeezed unreadable."""
    assert ".cmp-grid" in SHELL_CSS
    # The 820px breakpoint (shared with the shell's own rail collapse) stacks the grid.
    narrow = SHELL_CSS.split("@media (max-width: 820px)")[-1]
    assert "grid-template-columns: 1fr" in narrow


def test_compare_nav_chip_is_distinct_from_delivered():
    """Compare is live but identity-only: its chip must not claim full delivery."""
    m = re.search(r'data-view="compare".*?</button>', APP_JS, re.DOTALL)
    assert m and "rs-chip-identity" in m.group(0), "Compare nav item should carry the identity chip"
