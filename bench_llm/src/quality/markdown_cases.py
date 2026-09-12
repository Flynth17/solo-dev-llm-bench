"""BenchLLM V2 Markdown correctness cases (Act 11C-3A).

Exactly **20** independently identifiable markdown defects ``MD-01`` .. ``MD-20``,
each guarded by a single deterministic assertion that reports *why* it failed and
what the expected repair condition is.  The result shape mirrors the Python / Java
quality suites so the Results layer can render ``Markdown: 17/20`` with per-defect
evidence (rule id, line where meaningful, expected condition, actual problem).

Detection is **pure** -- no external linter, no subprocess, no disk.  The detectors
are small, self-contained regex checks over the fixture text.  This extends the same
deterministic quality path used by ``python_cases`` / ``java_cases``; it never touches
the active CLI-based benchmark in ``benchmark_markdown.py`` / ``task_markdown.py``.

Two bundled fixtures live next to this module::

    markdown_fixtures/broken_20.md       # exactly 20 defects (MD-01 .. MD-20)
    markdown_fixtures/corrected_20.md     # every defect repaired (20/20)

``validate(text)`` consumes raw markdown text and returns one :class:`CaseResult`
per defect in ``MD-01`` .. ``MD-20`` order.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable, Tuple

from ._common import (
    CaseDef,
    CaseResult,
    Check,
    FORMAT_VIOLATION,
    _is_blank,
    _split_lines_keep_eol,
)

# ------------------------------------------------------------------
# Fixture locations
# ------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "markdown_fixtures"
BROKEN_FIXTURE = FIXTURE_DIR / "broken_20.md"
CORRECTED_FIXTURE = FIXTURE_DIR / "corrected_20.md"


def _lines(text: str):
    return _split_lines_keep_eol(text)


def _visible(line: str) -> str:
    """Line content without the trailing newline marker."""
    return line.rstrip("\n")


# ------------------------------------------------------------------
# Local ATX heading detection (standard markdown uses a space after #).
# The shared ``_common`` helpers use ``#{1,6}\b`` which only matches hashes
# *directly* followed by a word char, so they reject normal "## Title"
# headings -- we need our own consistent matcher here.
# ------------------------------------------------------------------

_ATX_RE = re.compile(r"^(\s{0,3})(#{1,6})\s+(.*)$")


def _md_is_heading(line: str) -> bool:
    return _ATX_RE.match(_visible(line)) is not None


def _md_level(line: str) -> int | None:
    m = _ATX_RE.match(_visible(line))
    return len(m.group(2)) if m else None


def _md_text(line: str) -> str:
    m = _ATX_RE.match(_visible(line))
    return m.group(3).strip() if m else ""


_HEADING_TEXT_RE = re.compile(r"^\s*#+\s+")


def _heading_texts(text: str) -> list[str]:
    return [_md_text(ln) for ln in _lines(text) if _md_is_heading(ln)]


# ------------------------------------------------------------------
# Detectors -- each returns (passed, expected_condition, actual_problem)
# ------------------------------------------------------------------

_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _list_runs(raw: list, kind: str):
    """Maximal runs of consecutive list items.

    ``kind`` selects the matcher: 'bullet' (- * +), 'number' (1.), or 'any'.
    """
    if kind == "bullet":
        item_re = re.compile(r"^\s*([-*+]*)\s")
        def _match(s): return bool(re.match(r"^\s*[-*+]\s", s))
    elif kind == "number":
        def _match(s): return bool(re.match(r"^\s*\d+\.\s", s))
    else:  # any
        def _match(s): return bool(re.match(r"^\s*([-*+]|\d+\.)\s", s))

    runs = []
    start = None
    for i, s in enumerate(raw):
        if _match(s):
            if start is None:
                start = i
        else:
            if start is not None:
                runs.append((start, i - 1))
                start = None
    if start is not None:
        runs.append((start, len(raw) - 1))
    return runs


def det_md01(text: str) -> Tuple[bool, str, str]:
    """Heading hierarchy should never skip a level."""
    prev = None
    for ln in _lines(text):
        if not _md_is_heading(ln):
            continue
        lvl = _md_level(ln)
        if prev is not None and lvl > prev + 1:
            return False, (
                "a heading may only advance one level deeper than the previous heading"
            ), f"heading skips from level {prev} to level {lvl}"
        prev = lvl
    return True, "no heading skips a level", "heading levels advance by at most one"


def det_md02(text: str) -> Tuple[bool, str, str]:
    """An ATX heading marker must be followed by a space."""
    for ln in _lines(text):
        s = _visible(ln)
        if re.match(r"^#{1,6}\w", s):  # hash directly followed by a word char => no space
            return False, "a '#' heading marker must be followed by a single space", (
                f"line '{s}' has no space after the '#'"
            )
    return True, "every '#' heading is followed by a space", "no spacing defect found"


def det_md03(text: str) -> Tuple[bool, str, str]:
    """There must be exactly one top-level (H1) heading."""
    h1 = [ln for ln in _lines(text) if re.match(r"^\s*#\s+\S", _visible(ln))]
    if len(h1) >= 2:
        return False, "a document must contain exactly one H1 (top-level) heading", (
            f"found {len(h1)} top-level '# ' headings"
        )
    return True, "exactly one H1 heading present", "H1 count is correct"


def det_md04(text: str) -> Tuple[bool, str, str]:
    """A heading must be preceded by a blank line (except document start)."""
    vis = [_visible(ln) for ln in _lines(text)]
    for i, s in enumerate(vis):
        if not _md_is_heading(s):
            continue
        if i == 0:
            continue
        prev = vis[i - 1]
        if (
            prev.strip() != ""
            and not _md_is_heading(prev)
            and not prev.lstrip().startswith("<!--")
        ):
            return False, "a heading must be preceded by a blank line", (
                f"heading '{s}' has no blank line above it"
            )
    return True, "every heading is preceded by a blank line", "no spacing defect found"


def det_md05(text: str) -> Tuple[bool, str, str]:
    """An unordered list must use a single consistent marker (- * +)."""
    raw = [_visible(ln) for ln in _lines(text)]
    for start, end in _list_runs(raw, "bullet"):
        markers = {re.match(r"^\s*([-*+])", raw[i]).group(1) for i in range(start, end + 1)}
        if len(markers) > 1:
            return False, (
                f"an unordered list should use one consistent marker "
                f"(found mixed {sorted(markers)})"
            ), f"list mixes markers {sorted(markers)}"
    return True, "each unordered list uses a single marker", "no marker mixing found"


def det_md06(text: str) -> Tuple[bool, str, str]:
    """An ordered list must be sequential starting at 1 by step +1."""
    raw = [_visible(ln) for ln in _lines(text)]
    for start, end in _list_runs(raw, "number"):
        nums = [int(re.match(r"^\s*(\d+)\.\s", raw[i]).group(1))
                for i in range(start, end + 1)]
        expected = list(range(1, len(nums) + 1))
        if nums != expected:
            return False, "an ordered list must start at 1 and increment by one", (
                f"ordered list numbering {nums} is not sequential starting at 1"
            )
    return True, "every ordered list is sequential from 1", "no ordering defect found"


def det_md07(text: str) -> Tuple[bool, str, str]:
    """Lists must be surrounded by blank lines."""
    raw = [_visible(ln) for ln in _lines(text)]
    for start, end in _list_runs(raw, "any"):
        before = raw[start - 1] if start > 0 else None
        after = raw[end + 1] if end < len(raw) - 1 else None
        if before is not None and before.strip() != "":
            return False, "a list must be preceded by a blank line", (
                f"list starting at '{raw[start]}' has no blank line above it"
            )
        if after is not None and after.strip() != "":
            return False, "a list must be followed by a blank line", (
                f"list ending at '{raw[end]}' has no blank line below it"
            )
    return True, "every list is surrounded by blank lines", "no spacing defect found"


def det_md08(text: str) -> Tuple[bool, str, str]:
    """No trailing whitespace on any non-blank line."""
    for ln in _lines(text):
        s = _visible(ln)
        if s.strip() != "" and s != s.rstrip(" \t"):
            return False, "no line may contain trailing whitespace", (
                f"line ends with {len(s) - len(s.rstrip())} trailing space character(s)"
            )
    return True, "no line has trailing whitespace", "trailing-whitespace defect not found"


def det_md09(text: str) -> Tuple[bool, str, str]:
    """No more than one consecutive blank line."""
    maxrun = 0
    run = 0
    for ln in _lines(text):
        if _is_blank(ln):
            run += 1
            maxrun = max(maxrun, run)
        else:
            run = 0
    if maxrun >= 2:
        return False, "no more than one consecutive blank line is allowed", (
            f"found {maxrun} consecutive blank lines"
        )
    return True, "at most one consecutive blank line", "no excessive blanks found"


def det_md10(text: str) -> Tuple[bool, str, str]:
    """No bare URLs -- links must use markdown link syntax."""
    no_links = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
    no_autolinks = re.sub(r"<[^>]+>", "", no_links)
    m = re.search(r"https?://", no_autolinks)
    if m:
        return False, "a raw URL must be wrapped in markdown link syntax [text](url)", (
            f"bare URL found at position {m.start()}: '{m.group(0)}'"
        )
    return True, "all links use markdown link syntax", "no bare URL found"


def det_md11(text: str) -> Tuple[bool, str, str]:
    """Fenced code blocks must declare a language on the opening fence."""
    raw = [_visible(ln) for ln in _lines(text)]
    stack: list = []
    for s in raw:
        if _is_blank(s):
            continue
        m = re.match(r"^(\s*)(`{3,}|~{3,})(.*)$", s)
        if not m:
            continue
        fence, info = m.group(2), m.group(3).strip()
        if stack and fence[0] == stack[-1]["char"] and len(fence) >= stack[-1]["len"]:
            stack.pop()  # closing fence -- language is irrelevant here
            continue
        if info == "":
            return False, "a fenced code block must declare a language after the opening fence", (
                f"opening fence '{fence.strip()}' at this line has no language"
            )
        stack.append({"char": fence[0], "len": len(fence)})
    return True, "every fenced code block declares a language", "no unlabeled fence found"


def det_md12(text: str) -> Tuple[bool, str, str]:
    """All code fences should use the same delimiter style (backticks or tildes)."""
    raw = [_visible(ln) for ln in _lines(text)]
    stack: list = []
    styles = set()
    for s in raw:
        if _is_blank(s):
            continue
        m = re.match(r"^(\s*)(`{3,}|~{3,})(.*)$", s)
        if not m:
            continue
        fence = m.group(2)
        if stack and fence[0] == stack[-1]:
            stack.pop()
            continue
        styles.add(fence[0])
        stack.append(fence[0])
    if len(styles) > 1:
        return False, "all code fences should use the same delimiter style", (
            f"fence styles mixed across the document: {sorted(styles)}"
        )
    return True, "a single fence delimiter style is used", "no fence-style mixing found"


def det_md13(text: str) -> Tuple[bool, str, str]:
    """Full-line emphasis must not be used in place of a heading."""
    item_re = re.compile(r"^\s*([-*+]|\d+\.)\s")
    for ln in _lines(text):
        s = _visible(ln)
        if s.strip() == "" or _md_is_heading(s) or item_re.match(s):
            continue
        if re.match(r"^\*\*[^*]+\*\*$", s) or re.match(r"^\*[^*\n]+\*$", s):
            return False, "use an ATX heading (# ..), not emphasis, to title a section", (
                f"'{s}' uses emphasis where a heading is expected"
            )
    return True, "section titles use headings, not emphasis", "no emphasis-as-heading found"


def det_md14(text: str) -> Tuple[bool, str, str]:
    """No two headings may share identical normalized text."""
    texts = _heading_texts(text)
    counts = Counter(texts)
    dupes = [t for t, n in counts.items() if n >= 2]
    if dupes:
        return False, "no heading text may be duplicated", f"duplicate heading(s): {dupes}"
    return True, "all heading texts are unique", "no duplicate headings found"


def det_md15(text: str) -> Tuple[bool, str, str]:
    """Headings should not end with trailing punctuation."""
    for txt in _heading_texts(text):
        if txt and txt[-1] in ".,;:!?":
            return False, "a heading must not end with punctuation", (
                f"heading '{txt}' ends with punctuation"
            )
    return True, "no heading ends with punctuation", "no punctuation defect found"


def det_md16(text: str) -> Tuple[bool, str, str]:
    """List items should be indented consistently within a run."""
    raw = [_visible(ln) for ln in _lines(text)]
    for start, end in _list_runs(raw, "any"):
        indents = [len(s) - len(s.lstrip()) for s in raw[start:end + 1]]
        if any(x > 0 for x in indents) and any(x == 0 for x in indents):
            return False, "list items should share a consistent indentation column", (
                f"list item indentation {indents} mixes columns within one run"
            )
    return True, "each list run uses a single indentation column", "no inconsistent indent found"


def _pipe_table_blocks(raw: list) -> list:
    """Maximal runs of consecutive non-blank lines that contain a '|'."""
    blocks = []
    i, n = 0, len(raw)
    while i < n:
        s = raw[i]
        if "|" in s:
            start = i
            while i < n and not _is_blank(raw[i]):
                i += 1
            blocks.append((start, i - 1))
        else:
            i += 1
    return blocks


def det_md17(text: str) -> Tuple[bool, str, str]:
    """A pipe table must include a header separator row."""
    raw = [_visible(ln) for ln in _lines(text)]
    for start, end in _pipe_table_blocks(raw):
        block = [raw[i] for i in range(start, end + 1) if "|" in raw[i]]
        if len(block) >= 2 and not any(_SEP.match(x) for x in block):
            return False, "a pipe table must include a header separator row (| --- | ...)", (
                f"table starting at '{raw[start]}' has no separator row"
            )
    return True, "every pipe table has a separator row", "no malformed table found"


def det_md18(text: str) -> Tuple[bool, str, str]:
    """Every data row in a pipe table must have the same column count as its header."""
    raw = [_visible(ln) for ln in _lines(text)]

    def columns(s: str) -> int:
        return len([p for p in s.split("|") if p.strip() != ""])

    for start, end in _pipe_table_blocks(raw):
        block = [raw[i] for i in range(start, end + 1) if "|" in raw[i]]
        if any(_SEP.match(x) for x in block) and len(block) >= 2:
            header_cols = columns(block[0])
            for row in block[1:]:
                if _SEP.match(row):
                    continue
                if columns(row) != header_cols:
                    return False, "all rows of a pipe table must match the header column count", (
                        f"row '{row}' has {columns(row)} columns; header has {header_cols}"
                    )
    return True, "all table rows match the header column count", "no column-count mismatch found"


def det_md19(text: str) -> Tuple[bool, str, str]:
    """The document must end with exactly one trailing newline."""
    if text == "" or not text.endswith("\n"):
        return False, "the file should end with exactly one trailing newline", (
            "document does not end with a newline"
        )
    if text.endswith("\n\n"):
        return False, "the file should end with exactly one trailing newline", (
            "document ends with more than one blank line"
        )
    return True, "file ends with exactly one trailing newline", "trailing-newline defect not found"


def det_md20(text: str) -> Tuple[bool, str, str]:
    """No hard tabs -- indentation should use spaces."""
    for ln in _lines(text):
        s = _visible(ln)
        if "\t" in s and s.strip() != "":
            return False, "indentation should use spaces, not hard tabs", (
                f"a hard tab is used in '{s[:40]}'"
            )
    return True, "no hard tabs are used for indentation", "hard-tab defect not found"


# ------------------------------------------------------------------
# Registry: defect id -> detector
# ------------------------------------------------------------------

DETECTORS: dict[str, Callable[[str], Tuple[bool, str, str]]] = {
    "MD-01": det_md01,
    "MD-02": det_md02,
    "MD-03": det_md03,
    "MD-04": det_md04,
    "MD-05": det_md05,
    "MD-06": det_md06,
    "MD-07": det_md07,
    "MD-08": det_md08,
    "MD-09": det_md09,
    "MD-10": det_md10,
    "MD-11": det_md11,
    "MD-12": det_md12,
    "MD-13": det_md13,
    "MD-14": det_md14,
    "MD-15": det_md15,
    "MD-16": det_md16,
    "MD-17": det_md17,
    "MD-18": det_md18,
    "MD-19": det_md19,
    "MD-20": det_md20,
}

# ------------------------------------------------------------------
# Case definitions (MD-01 .. MD-20)
# ------------------------------------------------------------------

MARKDOWN_CASES: tuple[CaseDef, ...] = (
    CaseDef(id="MD-01", category="markdown", name="Heading hierarchy skips a level",
            capability="heading hierarchy",
            checks=(Check(label="skip-level", func="MD-01", args=(),
                          expected="each heading advances by at most one level"),)),
    CaseDef(id="MD-02", category="markdown", name="Missing space after heading marker",
            capability="atx heading spacing",
            checks=(Check(label="hash-space", func="MD-02", args=(),
                          expected="'#' followed by a space"))),
    CaseDef(id="MD-03", category="markdown", name="Multiple H1 headings",
            capability="single top-level heading",
            checks=(Check(label="one-h1", func="MD-03", args=(),
                          expected="exactly one H1 heading"))),
    CaseDef(id="MD-04", category="markdown", name="Missing blank line before heading",
            capability="heading surrounding blanks",
            checks=(Check(label="blank-before-heading", func="MD-04", args=(),
                          expected="heading preceded by a blank line"))),
    CaseDef(id="MD-05", category="markdown", name="Inconsistent unordered-list markers",
            capability="list marker consistency",
            checks=(Check(label="single-marker", func="MD-05", args=(),
                          expected="one consistent list marker per list"))),
    CaseDef(id="MD-06", category="markdown", name="Ordered-list numbering issue",
            capability="ordered list sequence",
            checks=(Check(label="ordered-seq", func="MD-06", args=(),
                          expected="ordered list sequential from 1"))),
    CaseDef(id="MD-07", category="markdown", name="Missing blank line around list",
            capability="list surrounding blanks",
            checks=(Check(label="blank-around-list", func="MD-07", args=(),
                          expected="list surrounded by blank lines"))),
    CaseDef(id="MD-08", category="markdown", name="Trailing whitespace",
            capability="no trailing whitespace",
            checks=(Check(label="no-trailing-ws", func="MD-08", args=(),
                          expected="no line ends with trailing whitespace"))),
    CaseDef(id="MD-09", category="markdown", name="Excessive blank lines",
            capability="single blank line limit",
            checks=(Check(label="max-one-blank", func="MD-09", args=(),
                          expected="at most one consecutive blank line"))),
    CaseDef(id="MD-10", category="markdown", name="Bare URL",
            capability="link syntax over bare URLs",
            checks=(Check(label="no-bare-url", func="MD-10", args=(),
                          expected="URL wrapped in link syntax"))),
    CaseDef(id="MD-11", category="markdown", name="Fenced code block missing language",
            capability="code fence language",
            checks=(Check(label="fence-lang", func="MD-11", args=(),
                          expected="fence declares a language"))),
    CaseDef(id="MD-12", category="markdown", name="Incorrect code fence style",
            capability="consistent fence delimiter",
            checks=(Check(label="fence-style", func="MD-12", args=(),
                          expected="single fence delimiter style"))),
    CaseDef(id="MD-13", category="markdown", name="Emphasis used instead of heading",
            capability="headings not emphasis",
            checks=(Check(label="no-emphasis-heading", func="MD-13", args=(),
                          expected="use '#' headings, not emphasis"))),
    CaseDef(id="MD-14", category="markdown", name="Duplicate heading",
            capability="unique heading text",
            checks=(Check(label="no-dup-heading", func="MD-14", args=(),
                          expected="unique heading text per document"))),
    CaseDef(id="MD-15", category="markdown", name="Heading punctuation issue",
            capability="heading punctuation",
            checks=(Check(label="no-hpunct", func="MD-15", args=(),
                          expected="headings end without punctuation"))),
    CaseDef(id="MD-16", category="markdown", name="Indentation issue in list",
            capability="consistent list indentation",
            checks=(Check(label="indent-consistent", func="MD-16", args=(),
                          expected="consistent item indentation within a run"))),
    CaseDef(id="MD-17", category="markdown", name="Malformed table (missing separator)",
            capability="table separator row",
            checks=(Check(label="has-separator", func="MD-17", args=(),
                          expected="pipe table has a separator row"))),
    CaseDef(id="MD-18", category="markdown", name="Inconsistent table column count",
            capability="table column consistency",
            checks=(Check(label="col-count", func="MD-18", args=(),
                          expected="all rows match header column count"))),
    CaseDef(id="MD-19", category="markdown", name="Missing final newline",
            capability="single trailing newline",
            checks=(Check(label="trailing-newline", func="MD-19", args=(),
                          expected="file ends with exactly one newline"))),
    CaseDef(id="MD-20", category="markdown", name="Hard tab indentation",
            capability="spaces not tabs",
            checks=(Check(label="no-hard-tab", func="MD-20", args=(),
                          expected="indentation uses spaces"))),
)


def validate(text: str) -> list[CaseResult]:
    """Validate raw *text* against all MD cases.

    Returns one :class:`CaseResult` per defect in ``MD-01`` .. ``MD-20`` order.
    Each result carries the detector's expected repair condition and, on failure,
    a concrete problem message (never an entire linter dump).
    """
    results: list[CaseResult] = []
    for case in MARKDOWN_CASES:
        passed, expected, actual = DETECTORS[case.id](text)
        if passed:
            results.append(CaseResult(
                case_id=case.id, category=case.category, name=case.name,
                passed=True, checks_passed=1, checks_total=1,
                expected=expected, actual="",
            ))
        else:
            results.append(CaseResult(
                case_id=case.id, category=case.category, name=case.name,
                passed=False, checks_passed=0, checks_total=1,
                failure_type=FORMAT_VIOLATION, expected=expected, actual=actual,
            ))
    return results


def all_defect_ids() -> list[str]:
    """All defect ids in stable order (MD-01 .. MD-20)."""
    return [case.id for case in MARKDOWN_CASES]


def load_broken_fixture() -> str:
    return BROKEN_FIXTURE.read_text(encoding="utf-8")


def load_corrected_fixture() -> str:
    return CORRECTED_FIXTURE.read_text(encoding="utf-8")
