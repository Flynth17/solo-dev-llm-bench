"""V2 live-response extraction adapters (Act 11C-4C2).

The frozen V2 validators in ``src.quality`` each consume a *specific* input shape:

    python_cases.validate(source)                 -> one module string
    java_cases.validate(source) / _run_case_test  -> one source per JAVA case
    markdown_cases.validate(text)                 -> the raw document text
    evidence_cases.validate(text)                 -> === EVID-XX / RESPONSE: blocks
    drift_cases.validate(text)                    -> the raw JSON-only response

A live model does not return those shapes directly. It wraps code in fenced
blocks, adds commentary around JSON, and presents a repaired document either as
plain text or inside one presentation fence. This module is the *smallest* layer
that converts each raw model response into the exact input the frozen validator
expects.

Design contract (never violated):

  * Extraction may remove PRESENTATION wrapping only: fenced code fences, an
    outer document fence, and surrounding prose.

  * Extraction must NEVER repair / improve / reinterpret / complete the model's
    SEMANTIC answer. It does not fix syntax, imports, missing symbols, whitespace
    inside the artifact, headings, tables, or a final newline. If it cannot find
    the artifact, it fails loudly instead of inventing one.

  * Extraction failures are observable (``ExtractionResult.failure_type`` /
    ``failure_reason``) so a later live run can distinguish "model produced wrong
    code" (extraction succeeds, validator reports compile_error / assertion_failure)
    from "adapter could not identify model output" (extraction_failure).

This module imports no LM Studio client, no persistence, and never calls or
mutates ``src.quality`` validators -- it only reads read-only metadata
(``all_function_names()``) to locate candidate code blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Read-only metadata: the set of symbols the frozen Python suite expects, used
# ONLY to recognise which fenced block is a python module (never leaked into a
# prompt, never serialized as an assertion).
from src.quality import python_cases as _py_cases
from src.quality._common import CaseResult, EXTRACTION_FAILURE

PYTHON_SYMBOLS = frozenset(_py_cases.all_function_names())

EXTRACTION_FAILURE_TYPE = "extraction_failure"


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of converting one raw model response into validator input.

    On success ``success=True`` and ``extracted_text`` holds the artifact to feed
    the frozen validator.  On failure ``success=False``, ``extracted_text=None``,
    and ``failure_type == "extraction_failure"`` with a human-readable reason.
    """

    success: bool
    extracted_text: Optional[str]
    failure_type: str = ""
    failure_reason: str = ""

    def __bool__(self) -> bool:  # convenient `if extract(...) is not None`-style use
        return self.success


def extraction_failure_result(case_id: str, category: str, name: str, reason: str) -> CaseResult:
    """Build a failed :class:`CaseResult` for an extraction failure.

    This is a pure helper for orchestration consumers so that "the adapter could
    not identify model output" is recorded with ``failure_type='extraction_failure'``
    instead of being conflated with a semantic wrong-answer. It constructs only a
    data object; it changes no scoring, persistence or Results-UI behaviour.
    """
    return CaseResult(
        case_id=case_id,
        category=category,
        name=name,
        passed=False,
        checks_passed=0,
        checks_total=0,
        failure_type=EXTRACTION_FAILURE_TYPE,
        failure_reason=reason,
    )


# ---------------------------------------------------------------------------
# Shared fence scanning (used by Python + Java extraction)
# ---------------------------------------------------------------------------

_FENCE_OPENER = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


def _iter_fenced_blocks(text: str) -> list[str]:
    """Return the inner text of every *balanced* fenced code block in order.

    A block is opened by a ``` or ~~~ line (optionally tagged with a language) and
    closed by a later line that contains only a fence of the same delimiter style.
    Unterminated fences are skipped (they yield no complete block). Surrounding
    prose / commentary outside fences is not returned here.
    """
    lines = text.split("\n")
    n = len(lines)
    blocks: list[str] = []
    i = 0
    while i < n:
        m = _FENCE_OPENER.match(lines[i])
        if not m:
            i += 1
            continue
        delim = m.group(1)[0]
        fence_len = len(m.group(1))
        closer_re = re.compile(r"^\s*(" + re.escape(delim) + r"{3,})\s*$")
        j = i + 1
        content: list[str] = []
        closed = False
        while j < n:
            cm = closer_re.match(lines[j])
            if cm and len(cm.group(1)) >= fence_len:
                closed = True
                break
            content.append(lines[j])
            j += 1
        if closed:
            blocks.append("\n".join(content))
            i = j + 1
        else:
            # Unterminated fence -> not a complete artifact; skip it.
            i += 1
    return blocks


def _looks_python(block: str) -> bool:
    """Heuristic: does *block* look like a Python module worth extracting?

    True if it declares at least one function, or mentions any expected PY symbol.
    Purely used to reject prose / non-python fenced blocks as candidates; never
    inspects the model's logic or fixes anything.
    """
    if re.search(r"(?m)^\s*(?:async\s+)?def\s+\w+", block):
        return True
    return any(sym in block for sym in PYTHON_SYMBOLS)


# ---------------------------------------------------------------------------
# 1. Python extraction -> module string for python_cases.validate()
# ---------------------------------------------------------------------------

def extract_python_module(raw: Optional[str]) -> ExtractionResult:
    """Extract the single Python module from a raw response to the combined task.

    Supports `````python```` fences and generic code fences with surrounding prose.
    Prefers the one block/module that contains the expected symbols. If two or more
    python-like fenced blocks are present, this is an ambiguous "competing partial
    answers" situation: rather than merge them (which would reinterpret the model's
    output), it fails.
    """
    if not isinstance(raw, str) or raw.strip() == "":
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE, "empty python response")

    blocks = _iter_fenced_blocks(raw)
    candidates = [b for b in blocks if _looks_python(b)]

    if blocks:  # the model wrapped its code in at least one fence
        if not candidates:
            return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                    "no python code found in provided code block(s)")
        if len(candidates) == 1:
            return ExtractionResult(True, candidates[0].strip(), "", "")
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                f"multiple python code blocks ({len(candidates)}); "
                                "refusing to merge partial answers")

    # No fenced block at all -> treat the whole text as a plain module (the common
    # "clean reference source" case), but only if it actually looks like Python.
    whole = raw.strip()
    if _looks_python(whole):
        return ExtractionResult(True, whole, "", "")
    return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                            "no python code found (no code block and no def/class present)")


# ---------------------------------------------------------------------------
# 2. Java extraction -> one source string containing class Solution
# ---------------------------------------------------------------------------

_JAVA_SOLUTION_RE = re.compile(r"\bclass\s+Solution\b")


def extract_java_solution(raw: Optional[str]) -> ExtractionResult:
    """Extract the single ``class Solution`` source from a raw per-case response.

    Supports `````java```` fences and generic code fences with surrounding
    commentary. The model must supply its own ``class Solution``; this function
    never synthesises it, fixes imports, repairs syntax, renames methods, or
    combines multiple competing solutions. Ambiguous (multiple) or missing
    ``class Solution`` -> extraction failure.
    """
    if not isinstance(raw, str) or raw.strip() == "":
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE, "empty java response")

    blocks = _iter_fenced_blocks(raw)
    candidates = [b for b in blocks if _JAVA_SOLUTION_RE.search(b)]

    if blocks:  # at least one fenced region was present
        if not candidates:
            return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                    "class Solution not found in provided code block(s)")
        if len(candidates) == 1:
            return ExtractionResult(True, candidates[0].strip(), "", "")
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                f"multiple candidate 'Solution' sources ({len(candidates)}); "
                                "refusing to merge competing solutions")

    # No fence -> whole text is the source only if it actually contains Solution.
    whole = raw.strip()
    if _JAVA_SOLUTION_RE.search(whole):
        return ExtractionResult(True, whole, "", "")
    return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                            "class Solution not found (no code block and no 'class Solution' present)")


# ---------------------------------------------------------------------------
# 3. Markdown extraction -> the candidate repaired document
# ---------------------------------------------------------------------------

_MD_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")


def _line_start_offsets(text: str) -> list[int]:
    """Character offsets just past each line's terminating '\\n'."""
    offs = [0]
    i = 0
    while True:
        p = text.find("\n", i)
        if p < 0:
            break
        offs.append(p + 1)
        i = p + 1
    return offs


def extract_markdown_document(raw: Optional[str]) -> ExtractionResult:
    """Extract the candidate repaired Markdown document from a raw response.

    If the model wrapped the *entire* document in a single code/markdown fence
    (first non-blank line is an opener, last content line is its matching closer),
    only that outer presentation pair is unwrapped -- everything between them is
    returned verbatim (whitespace, headings, tables, final newline preserved). If
    there is no enclosing fence the response is passed through unchanged. Nothing
    inside the document is repaired.
    """
    if not isinstance(raw, str) or raw.strip() == "":
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE, "empty markdown response")

    lines = raw.split("\n")
    n = len(lines)

    # The enclosing fence must begin at the first non-blank line; leading prose
    # before a fence means it is not wrapping the whole document.
    o = 0
    while o < n and lines[o].strip() == "":
        o += 1
    if o >= n:
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE, "no markdown content")

    open_m = _MD_FENCE_RE.match(lines[o])
    if not open_m:
        # No outer presentation fence -> pass the document through unchanged.
        return ExtractionResult(True, raw, "", "")

    delim = open_m.group(2)[0]
    fence_len = len(open_m.group(2))
    closer_re = re.compile(r"^\s*(" + re.escape(delim) + r"{3,})\s*$")

    c = o + 1
    closer_idx = None
    while c < n:
        cm = closer_re.match(lines[c])
        if cm and len(cm.group(1)) >= fence_len:
            closer_idx = c
            break
        c += 1

    if closer_idx is None:
        # Opening fence with no matching closer -> do not corrupt the document.
        return ExtractionResult(True, raw, "", "")

    offs = _line_start_offsets(raw)
    inner = raw[offs[o + 1]:offs[closer_idx]]
    if inner.strip() == "":
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                "wrapper contained no document content")
    return ExtractionResult(True, inner, "", "")


# ---------------------------------------------------------------------------
# 4. Evidence pass-through (unchanged except strict transport identity)
# ---------------------------------------------------------------------------

def extract_evidence_response(raw: Optional[str]) -> ExtractionResult:
    """Pass the model's evidence response through unchanged.

    The existing ``evidence_cases.render_input()`` remains responsible for
    assembling gradeable blocks; the free-text answer itself is never rewritten,
    cleaned or rephrased -- only identity is preserved so grading sees exactly what
    the model wrote.
    """
    if not isinstance(raw, str):
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                "evidence response was not a string")
    return ExtractionResult(True, raw, "", "")


# ---------------------------------------------------------------------------
# 5. DRIFT pass-through (raw JSON-only response, commentary stays visible)
# ---------------------------------------------------------------------------

def extract_drift_response(raw: Optional[str]) -> ExtractionResult:
    """Pass the model's DRIFT-01 response through to the frozen validator verbatim.

    No JSON normalisation and no ``"answer"`` extraction: output-format discipline
    is part of DRIFT-01, so any commentary around the JSON must remain visible to
    ``drift_cases.validate()`` exactly as delivered.
    """
    if not isinstance(raw, str):
        return ExtractionResult(False, None, EXTRACTION_FAILURE_TYPE,
                                "drift response was not a string")
    return ExtractionResult(True, raw, "", "")


# ---------------------------------------------------------------------------
# Suite dispatch (for symmetry / observability of the adapter layer)
# ---------------------------------------------------------------------------

def extract_for_suite(suite_key: str, raw: Optional[str]) -> ExtractionResult:
    """Route one raw response to its suite adapter by ``suite_key``.

    Keys: "python", "java", "markdown", "evidence", "drift". Unknown keys raise
    ``ValueError`` rather than guessing -- a wrong-key call is itself a bug worth
    surfacing.
    """
    return {
        "python": extract_python_module,
        "java": extract_java_solution,
        "markdown": extract_markdown_document,
        "evidence": extract_evidence_response,
        "drift": extract_drift_response,
    }[suite_key](raw)
