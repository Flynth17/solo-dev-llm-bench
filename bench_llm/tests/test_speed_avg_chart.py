"""Average Generation Throughput summary chart on the Speed results page.

The chart is a presentation-only horizontal bar summary rendered ABOVE the compact
benchmark table: one bar per visible model/config row, fixed 0-500 tok/s scale, same
authoritative average values as the table, respecting current filters and legacy
exclusion. Missing / unsupported / invalid averages render as an explicit gap -- never
zero. No quality verdict language on this surface.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
RESULTS_HTML = (STATIC_DIR / "results.html").read_text(encoding="utf-8")
RESULTS_JS = (STATIC_DIR / "results.js").read_text(encoding="utf-8")


def _chart_block() -> str:
    """The chart section of results.js (constants + renderAvgChart)."""
    start = RESULTS_JS.index("// Average Generation Throughput summary")
    end = RESULTS_JS.index("// Table rendering")
    return RESULTS_JS[start:end]


def test_chart_container_mounted_above_benchmark_table():
    assert "id=\"speed-avg-chart\"" in RESULTS_HTML
    # The chart sits between the panel header and the table -- above it, inside the same
    # panel, so hiding the panel (empty state) hides the chart too.
    head = RESULTS_HTML.index("rs-bench-head")
    chart = RESULTS_HTML.index("id=\"speed-avg-chart\"")
    table = RESULTS_HTML.index("id=\"benchmark-table\"")
    assert head < chart < table


def test_chart_uses_fixed_zero_to_500_scale():
    block = _chart_block()
    assert "var AVG_CHART_MAX = 500" in block
    assert "AVG_CHART_TICKS = [0, 100, 200, 300, 400, 500]" in block
    # Bar width is a direct fraction of the FIXED range -- stable across filters/sorts,
    # never rescaled to the visible maximum.
    assert "(r.avg / AVG_CHART_MAX) * 100" in block


def test_chart_renders_one_bar_per_visible_row_with_table_values():
    """The chart is rendered from the exact same rows array as the table body, so it
    inherits filters, sort order and legacy exclusion; values are the authoritative avg."""
    # renderTable passes its own (filtered + sorted) rows to the chart.
    assert "renderAvgChart(rows)" in RESULTS_JS
    block = _chart_block()
    # Bars map back to table rows by run id for click-to-row interaction.
    assert "data-run-id" in block


def test_chart_missing_values_never_rendered_as_zero():
    block = _chart_block()
    # Null average -> explicit gap (em-dash) and no bar fill -- never coerced to 0.
    assert "r.avg == null" in block
    assert "\\u2014" in block


def test_chart_inherits_legacy_exclusion():
    """The chart renders only what renderTable receives; legacy runs are already excluded
    upstream (buildBenchmarkRuns -> isNonLegacyVersion), so they never reach the chart."""
    assert "isNonLegacyVersion" in RESULTS_JS
    # The chart performs no run-level filtering of its own -- single source of truth.
    assert "metric_version" not in _chart_block()


def test_chart_has_no_verdict_language():
    """Neutral benchmark summary only: no winner/best/ranking wording on this surface."""
    lowered = _chart_block().lower()
    for token in ("winner", "best", "ranking", "leader"):
        assert token not in lowered, f"chart block must stay verdict-free: {token!r}"


def test_chart_click_maps_to_table_row():
    """Clicking a bar scrolls to and briefly highlights the matching table row."""
    block = _chart_block()
    assert "scrollIntoView" in block
    assert "rs-bench-row-highlight" in block
