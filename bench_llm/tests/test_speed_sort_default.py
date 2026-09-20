"""Default sort mode on the Speed Results page must be 'Avg gen tok/s (high-low)'.

The Speed Results page used to default to alphabetical ('Model A-Z') sorting, which made
the result rows and the Average Generation Throughput chart appear alphabetically rather
than fastest-first. This pins the initial/default sort mode to the existing 'avg' sort so
results render highest average generation speed first on load. The 'model' option stays
available in the dropdown; only the *default* changes.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
RESULTS_HTML = (STATIC_DIR / "results.html").read_text(encoding="utf-8")
RESULTS_JS = (STATIC_DIR / "results.js").read_text(encoding="utf-8")


def test_avg_option_is_the_default_selected_in_dropdown():
    # The 'avg' option carries the selected attribute; the 'model' option does not.
    assert '<option value="avg" selected>' in RESULTS_HTML
    assert '<option value="model">' in RESULTS_HTML
    assert '<option value="model" selected>' not in RESULTS_HTML


def test_render_results_defaults_to_avg_when_select_unavailable():
    # Defensive fallback: even if the <select> were missing/empty, renderResults must pick
    # 'avg' (fastest-first) rather than the old alphabetical default.
    assert 'var mode = speedSort ? speedSort.value : "avg";' in RESULTS_JS


def test_model_option_still_present_and_selectable():
    # Requirement: 'Model (A-Z)' remains available; only its selected state changed.
    assert 'value="model"' in RESULTS_HTML
