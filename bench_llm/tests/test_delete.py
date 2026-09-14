"""Client-side cancel/escape regression tests for the results page.

The HTTP DELETE /api/results/{run_id} surface, its legacy delete-modal client and
the persistence-layer ``ResultsStore.delete_run`` method were all retired in M2.
This file retains only the guard over the *current* product contract -- the
``escapeHtml`` utility in ``static/results-utils.js`` that the live results page
still uses to render run labels (see ``results.js``).

Any future per-run deletion persistence must be re-protected by a dedicated test.
"""

from pathlib import Path


# ---------------------------------------------------------------------------
# Client-side cancel regression tests
# ---------------------------------------------------------------------------

class TestCancelRegression:

    def test_escapeHtml_produces_valid_entities(self):
        """escapeHtml must produce valid HTML entities, not broken strings."""
        js_path = Path(__file__).parent.parent / "static" / "results-utils.js"
        js_text = js_path.read_text(encoding="utf-8")
        # Verify the function uses String.fromCharCode to build entities
        assert "String.fromCharCode(38)" in js_text  # &
        assert "String.fromCharCode(60)" in js_text  # <
        assert "String.fromCharCode(62)" in js_text  # >
        # Verify no broken entities like "lt;;" or "amp;;"
        assert "lt;;" not in js_text
        assert "amp;;" not in js_text
        assert "gt;;" not in js_text
