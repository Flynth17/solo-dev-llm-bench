"""Cross-surface Results UX contract for the P7 responsive + accessibility cleanup.

Locks in the P7 priorities against static assets (no browser required), mirroring
the static-asset approach used by ``test_comparison_ui.py`` and ``test_app_shell.py``:

  * a global ``:focus-visible`` baseline on every interactive control
    (design-system §10) — this is the one genuine accessibility gap found in P7;
  * narrow-width responsive handling present on every surface (no horizontal overflow);
  * shared P1 status vocabulary rendered as text, never colour alone (§5.1);
  * no winner / composite / leader verdict language introduced by the cleanup;
  * Context placeholder stays honest — no fabricated aggregate projection (P5 BLOCKED).

These assertions are intentionally narrow: they guard the invariants P7 must not
break rather than re-litigating the already-delivered surfaces.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

import src.main  # noqa: E402,F401

_PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = _PROJECT_ROOT / "static"
CLIENT = TestClient(src.main.app)

SHARED_CSS = (STATIC_DIR / "shared.css").read_text(encoding="utf-8")
COMPARE_JS = (STATIC_DIR / "results-compare.js").read_text(encoding="utf-8")
SPEED_JS = (STATIC_DIR / "speed-result.js").read_text(encoding="utf-8")
V2_JS = (STATIC_DIR / "v2-result.js").read_text(encoding="utf-8")
SHELL_JS = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")
STATUS_JS = (STATIC_DIR / "results-status.js").read_text(encoding="utf-8")

# Every surface that must carry narrow-width handling so cards/tables reflow
# instead of forcing a page-level horizontal scroll.
SURFACE_CSS = {
    "Overall/Shell": "results-shell.css",
    "Speed": "speed-result.css",
    "Workflow/V2": "v2-result.css",
    "Context": "context-result.css",
}

# design-system §10: a visible focus ring on every interactive control.
_FOCUS_RULE = re.compile(r":focus-visible\s*\{[^}]*outline", re.DOTALL)
_INTERACTIVE_FOCUS_SELECTORS = re.compile(
    r"(?:^|\n)\s*(?:a|button|select|input|\[tabindex\]):focus-visible"
)

# A narrow-width media block exists on the surface (max-width query).
_NARROW_MEDIA = re.compile(r"@media\s*\([^)]*max-width:\s*\d+px")

# Shared P1 status vocabulary — human words, never colour alone.
_STATUS_WORDS = ["completed", "partial", "unsupported", "unavailable"]

# No winner / composite / leader verdict language introduced by the cleanup.
_FORBIDDEN_VERDICT_TOKENS = [
    "winner",
    "leader",
    "composite score",
    "better model",
    "faster model",
]


# ---------------------------------------------------------------------------
# 1. Focus-visible baseline (accessibility) — the P7 gap that was fixed
# ---------------------------------------------------------------------------

def test_shared_css_carries_a_global_focus_visible_baseline():
    """shared.css defines a global :focus-visible rule with an outline."""
    assert _FOCUS_RULE.search(SHARED_CSS), "shared.css must define a global :focus-visible rule"


def test_focus_baseline_covers_interactive_controls():
    """The baseline covers the interactive control families, not one selector."""
    assert _INTERACTIVE_FOCUS_SELECTORS.search(SHARED_CSS), (
        "global focus baseline must cover interactive controls "
        "(a/button/select/input/[tabindex])"
    )


def test_focus_baseline_uses_a_theme_fallback():
    """Dark Results shell exposes --rs-accent; light Dashboard falls back to a ring."""
    assert "--rs-accent," in SHARED_CSS, (
        "focus baseline must fall back when the dark token is absent (light dashboard)"
    )


def test_every_shellled_page_loads_shared_css():
    """Every shell page loads shared.css so the focus baseline applies everywhere."""
    for path in ["/", "/results", "/speed/results/any-run-id", "/v2/results/any-run-id"]:
        body = CLIENT.get(path).text
        assert "/static/shared.css" in body, f"{path}: shared.css not loaded -> no focus baseline"


# ---------------------------------------------------------------------------
# 2. Responsive handling per surface (no horizontal overflow)
# ---------------------------------------------------------------------------

def test_every_surface_has_narrow_width_handling():
    for name, fname in SURFACE_CSS.items():
        css = (STATIC_DIR / fname).read_text(encoding="utf-8")
        assert _NARROW_MEDIA.search(css), (
            f"{name} ({fname}): no max-width media block -> overflow risk at narrow widths"
        )


# ---------------------------------------------------------------------------
# 3. Shared status vocabulary rendered as text, never colour alone
# ---------------------------------------------------------------------------

def test_shared_status_vocabulary_present_as_text():
    lowered = STATUS_JS.lower()
    for word in _STATUS_WORDS:
        assert word in lowered, f"shared status vocabulary missing a human word: {word}"


# ---------------------------------------------------------------------------
# 4. No winner / composite / leader verdict language introduced
# ---------------------------------------------------------------------------

def test_no_verdict_language_in_rendering_surfaces():
    """The pure rendering surfaces carry no verdict language (P7 must not reintroduce it)."""
    for label, js in [("Compare", COMPARE_JS), ("Speed", SPEED_JS), ("V2/Workflow", V2_JS)]:
        lowered = js.lower()
        for token in _FORBIDDEN_VERDICT_TOKENS:
            assert token not in lowered, f"{label} introduced verdict language: {token!r}"


# ---------------------------------------------------------------------------
# 5. Context placeholder stays honest — no fabricated aggregate projection (P5)
# ---------------------------------------------------------------------------

def test_context_placeholder_states_runs_are_not_projected():
    """Placeholder honestly states the ranking read model does not project Context runs."""
    assert "does not yet project" in SHELL_JS, (
        "Context placeholder must state runs are not projected into an aggregate list"
    )


def test_context_placeholder_does_not_invent_data():
    """Placeholder states no data is invented; N/A stays N/A."""
    assert "no data is invented" in SHELL_JS.lower(), (
        "Context placeholder must state that no data is invented"
    )
