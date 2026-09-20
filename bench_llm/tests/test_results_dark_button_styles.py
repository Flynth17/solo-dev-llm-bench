"""Dark-theme button/code overrides on the Speed Results surface.

The Details table buttons and modal Close button used .btn-secondary (shared.css), a
light-theme style that renders as bright white blocks inside the dark results shell; the
modal Run ID <code> inherited the global light code chip, putting near-white text on a
near-white background (invisible value). These tests pin the scoped dark overrides in
results.css so the light styles cannot silently return.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
RESULTS_CSS = (STATIC_DIR / "results.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """Return the CSS rule body for a selector (first occurrence)."""
    idx = RESULTS_CSS.index(selector)
    brace = RESULTS_CSS.index("{", idx)
    end = RESULTS_CSS.index("}", brace)
    return RESULTS_CSS[brace:end]


def test_detail_button_is_dark_ghost():
    body = _rule(".rs-detail-btn {")
    assert "background: transparent" in body
    assert "var(--rs-border-soft)" in body
    # Hover affordance must stay dark-themed, not the light .btn-secondary hover.
    hover = _rule(".rs-detail-btn:hover {")
    assert "var(--rs-accent-hover)" in hover


def test_modal_close_button_is_dark_ghost():
    body = _rule("#speed-detail-close {")
    assert "background: transparent" in body
    assert "var(--rs-border-soft)" in body


def test_clear_filters_buttons_are_dark_ghosts():
    # Both filter bars (speed + workflow) must not keep the light .btn-secondary look.
    for selector in ('#clear-filters', '#wf-clear-filters'):
        body = _rule(selector)
        assert "background: transparent" in body
        assert "var(--rs-border-soft)" in body


def test_modal_code_chip_is_dark_and_readable():
    # Run ID <code> must not inherit the light global chip (white-on-white).
    body = _rule("#speed-detail-content code {")
    assert "background: var(--rs-surface-2)" in body
    assert "color: var(--rs-text)" in body
