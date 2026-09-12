"""Canonical model-facing task specifications for the frozen V2 quality corpus.

This module is a **stimulus layer only**. It contains *prompt / task definitions*
and tiny rendering helpers that turn each definition into exactly what would be
sent to a live model. It deliberately does **not**:

* call LM Studio or any network endpoint,
* import persistence (results / routes / sqlite / csv),
* import or invoke the frozen quality validators (``src.quality.*``), nor mutate
  them -- it only *reuses verbatim* the scenario text those suites already ship,
* contain answer keys, reference implementations, or the frozen grading
  assertions (``Check.args`` / ``Check.expected``).

Design principle (no hidden-test leakage): for the code suites the model is told
the required symbol name, an input/output contract, behavioural requirements and
the edge-case semantics *necessary to make the task unambiguous*. It is NOT told
the complete hidden assertion set. The frozen checks are used only to *understand*
the intended specification -- never to serialize the tests into a prompt.

Live-request topology this module encodes (see :func:`live_request_topology`):

    python      1   (all 10 specs combined into one module request)
    java        12  (one independent per-case request; solutions collide if merged)
    markdown    1   (one full-document repair request -> yields all defect results)
    evidence    10  (one per EVID scenario, each independently graded)
    drift       1   (one instruction block -> one JSON-only response)

Total = 25 live model requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

# ---------------------------------------------------------------------------
# Reuse verbatim: scenario text already shipped by the frozen suites.
# Importing here is *reuse*, not modification -- we never call validate() nor
# read Check.args/expected, and we pull only the public scenario factories.
# ---------------------------------------------------------------------------
from src.quality import evidence_cases as _evidence   # noqa: E402  (verbatim reuse)
from src.quality import drift_cases as _drift          # noqa: E402  (verbatim reuse)


# ===========================================================================
# Python canonical specs (PY-01 .. PY-10)
# Each spec intentionally omits the concrete test input/output vectors.
# ===========================================================================

@dataclass(frozen=True)
class PythonPromptSpec:
    id: str
    function_name: str
    signature: str          # human-readable signature expectation
    behaviour: str          # concise natural-language behaviour
    edge_cases: str         # edge-case semantics necessary for ambiguity
    output_requirement: str


PYTHON_SPECS: tuple[PythonPromptSpec, ...] = (
    PythonPromptSpec(
        id="PY-01",
        function_name="split_camel",
        signature="split_camel(s: str) -> list[str]",
        behaviour=(
            "Split a camelCase / PascalCase identifier into its constituent word "
            "tokens, preserving the original casing of each token. A run of "
            "uppercase letters forms one acronym-style token; a following "
            "capitalized word (an uppercase letter followed by lowercase letters) "
            "begins a new token; lower-case runs become tokens as-is."
        ),
        edge_cases=(
            "An empty string yields no words (an empty list). Identifiers that "
            "contain non-alphabetic characters such as underscores are kept "
            "attached within their surrounding token rather than split on them. "
            "A single all-lower-case or a single capitalized identifier stays intact."
        ),
        output_requirement="Return a Python list of the word tokens in order.",
    ),
    PythonPromptSpec(
        id="PY-02",
        function_name="unique_preserve_order",
        signature="unique_preserve_order(seq) -> list",
        behaviour=(
            "Return a new list containing the elements of the input with duplicate "
            "values removed, keeping only the first occurrence of each distinct "
            "value and preserving their original relative order."
        ),
        edge_cases=(
            "An empty input yields an empty list; an all-equal input collapses to a "
            "single element. The behaviour is defined for any hashable values while "
            "preserving first-occurrence order."
        ),
        output_requirement="Return the de-duplicated list in first-occurrence order.",
    ),
    PythonPromptSpec(
        id="PY-03",
        function_name="nested_sum",
        signature="nested_sum(n) -> int",
        behaviour=(
            "Return the sum of every integer contained in n, where n may be a list "
            "that itself contains further lists at any depth; summation recurses "
            "through nesting."
        ),
        edge_cases=(
            "Empty inner lists contribute nothing (zero). Negative numbers and zero "
            "are summed normally, and arbitrarily deeply nested structures are handled."
        ),
        output_requirement="Return the integer total.",
    ),
    PythonPromptSpec(
        id="PY-04",
        function_name="factorial",
        signature="factorial(n) -> int | None",
        behaviour=(
            "Return the factorial of n -- the product of all positive integers from "
            "1 up to n."
        ),
        edge_cases=(
            "Both 0 and 1 are defined as 1 (the empty product). Negative inputs yield "
            "None rather than raising an error."
        ),
        output_requirement="Return the factorial as an int, or None for negative input.",
    ),
    PythonPromptSpec(
        id="PY-05",
        function_name="partition_parity",
        signature="partition_parity(nums) -> dict[str, list]",
        behaviour=(
            "Partition the numbers into two groups by parity and return them under "
            "the fixed keys 'even' and 'odd', preserving each group's original order."
        ),
        edge_cases=(
            "Both keys are always present; a category with no members yields an empty "
            "list. Zero counts as even and negative even/odd numbers keep their sign "
            "and position. An empty input yields both lists empty."
        ),
        output_requirement='Return {"even": [...], "odd": [...]}.',
    ),
    PythonPromptSpec(
        id="PY-06",
        function_name="apply_operations",
        signature="apply_operations(start, ops) -> int",
        behaviour=(
            "Starting from the initial numeric value, apply a sequence of named "
            "arithmetic operations in order, returning the running total after each "
            "operation is applied. Operations are strictly sequential."
        ),
        edge_cases=(
            "Operations are drawn from add, subtract and multiply; each pairs an "
            "operator name with an operand. Applying no operations returns the initial "
            "value unchanged."
        ),
        output_requirement="Return the final numeric total.",
    ),
    PythonPromptSpec(
        id="PY-07",
        function_name="is_valid_email",
        signature="is_valid_email(s: str) -> bool",
        behaviour=(
            "Return True only when s is a string shaped as a single local part and a "
            "single domain part separated by exactly one '@'."
        ),
        edge_cases=(
            "Neither the local nor the domain may contain spaces or lead/trail with a "
            "dot; the domain must contain at least one dot; both parts must be non-empty. "
            "Empty or otherwise malformed input returns False."
        ),
        output_requirement="Return a boolean.",
    ),
    PythonPromptSpec(
        id="PY-08",
        function_name="safe_int_parse",
        signature="safe_int_parse(s) -> int | None",
        behaviour=(
            "Return the base-ten integer value of s after trimming surrounding whitespace."
        ),
        edge_cases=(
            "Only base-ten integers are accepted; inputs that are empty, contain a "
            "decimal point, use hexadecimal notation, or are otherwise not valid base-"
            "ten integers (including None / non-string types) yield None. Parsing never raises."
        ),
        output_requirement="Return the parsed int, or None when parsing fails.",
    ),
    PythonPromptSpec(
        id="PY-09",
        function_name="classify_temperature",
        signature="classify_temperature(c: float) -> str",
        behaviour=(
            "Classify a Celsius temperature into its water phase state."
        ),
        edge_cases=(
            "Values below 0 return 'solid'; from 0 up to but not including 100 return "
            "'liquid'; 100 and above return 'gas'. Boundaries are inclusive on the "
            "upper edges of each lower band (0 is liquid, 100 is gas). Fractional "
            "temperatures follow the same bands."
        ),
        output_requirement="Return one of 'solid', 'liquid', 'gas'.",
    ),
    PythonPromptSpec(
        id="PY-10",
        function_name="second_largest",
        signature="second_largest(lst) -> int | None",
        behaviour=(
            "Return the second-largest distinct value from a non-empty numeric list."
        ),
        edge_cases=(
            "Distinctness ignores duplicate maximums, so a list whose top values repeat "
            "still yields the next smaller distinct value. If there are fewer than two "
            "distinct values -- including all-equal or single-element inputs -- return "
            "None. Negatives sort by magnitude."
        ),
        output_requirement="Return an int, or None when fewer than two distinct values exist.",
    ),
)


# ===========================================================================
# Java canonical specs (JAVA-01 .. JAVA-12)
# Each requires returning only compilable Solution source.
# ===========================================================================

@dataclass(frozen=True)
class JavaPromptSpec:
    id: str
    method_signature: str     # required public static method signature on Solution
    behaviour: str            # input/output behaviour
    edge_cases: str           # necessary edge cases / contract nuances
    output_restriction: str   # always: return only compilable Java source


JAVA_SPECS: tuple[JavaPromptSpec, ...] = (
    JavaPromptSpec(
        id="JAVA-01",
        method_signature="public static boolean is_palindrome(String s)",
        behaviour=(
            "Return whether the string reads identically forwards and backwards, "
            "compared case-insensitively."
        ),
        edge_cases=(
            "A null or empty string is treated as a palindrome (true)."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-02",
        method_signature="public static Map<String, Integer> groupByFirstLetter(List<String> words)",
        behaviour=(
            "Build a frequency map keyed by each word's first letter; the key is the "
            "lowercased form of that first character and the value is how many words "
            "begin with that letter. Preserve insertion order (first appearance)."
        ),
        edge_cases=(
            "Skip null or empty words. Empty input yields an empty map. Mixed-case "
            "words are grouped under their lowercased first-letter key."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method; "
            "use a LinkedHashMap for stable ordering."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-03",
        method_signature="public static String classifySeverity(String code)",
        behaviour=(
            "Map a single-letter severity indicator to its canonical uppercase severity name."
        ),
        edge_cases=(
            "Recognized codes are D->DEBUG, I->INFO, W->WARN, E->ERROR (uppercase). Any "
            "other code returns UNKNOWN. NOTE: this is a fixed domain vocabulary with no "
            "derivable rule, so the full set of recognized codes and their names is part "
            "of the task specification rather than hidden test data."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-04",
        method_signature="public static int safe_length(String s)",
        behaviour="Return the character length of the string.",
        edge_cases=(
            "A null input returns 0 instead of raising; whitespace-only strings count "
            "their actual characters."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-05",
        method_signature="public static Double safe_divide(double a, double b)",
        behaviour=(
            "Return the floating-point quotient of a divided by b as a nullable value."
        ),
        edge_cases=(
            "When the divisor is zero -- including when both operands are zero -- return "
            "null instead of raising an arithmetic error; otherwise return the exact quotient."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-06",
        method_signature="public static double distance(double x1, double y1, double x2, double y2)",
        behaviour=(
            "Return the Euclidean distance between the 2D points (x1, y1) and (x2, y2)."
        ),
        edge_cases=(
            "Handle zero-distance, axis-aligned, negative coordinates and non-round "
            "results with full floating-point precision."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-07",
        method_signature="public static List<String> sortByLengthThenLexicographic(List<String> words)",
        behaviour=(
            "Return a new list sorted primarily by string length in ascending order, "
            "breaking ties by natural (lexicographic) order. Strings equal in length and "
            "text keep their original relative order (stable sort)."
        ),
        edge_cases=(
            "Empty input yields an empty list; all-same-length inputs sort purely "
            "lexicographically while preserving input order for exact duplicates."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-08",
        method_signature="public static List<Integer> flattenNested(List<List<Integer>> matrix)",
        behaviour=(
            "Flatten a two-level structure into a single list in row-major order -- each "
            "row's elements are appended in their original order."
        ),
        edge_cases=(
            "Null rows and empty sublists contribute no elements; an empty matrix yields "
            "an empty list."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-09",
        method_signature="public static int runOperations(List<String> ops)",
        behaviour=(
            "Maintain an integer counter starting at zero and apply each operation in "
            "order: 'inc' increments by one, 'dec' decrements by one, 'reset' sets the "
            "counter to zero."
        ),
        edge_cases=(
            "Any unrecognized operation is ignored (no effect). Empty input returns zero."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-10",
        method_signature="public static int daysBetween(String from, String to)",
        behaviour=(
            "Return the number of days to advance forward within the week to reach `to` "
            "from `from`, computed cyclically over MONDAY..SUNDAY, giving a result in 0..6."
        ),
        edge_cases=(
            "An unrecognized weekday name for either argument returns -1. Wrap-around is "
            "handled (for example SUNDAY to MONDAY is 1)."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method; "
            "the valid names are the standard seven weekday names in uppercase."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-11",
        method_signature="public static boolean allWithin(List<Integer> values, int lo, int hi)",
        behaviour=(
            "Return True only if every integer lies within the closed interval [lo, hi] inclusive."
        ),
        edge_cases=(
            "An empty list returns True vacuously; a single value outside the range "
            "returns False. Boundaries are inclusive."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
    JavaPromptSpec(
        id="JAVA-12",
        method_signature="public static List<Integer> parseRecords(List<String> lines)",
        behaviour=(
            "For each line of the form '<identifier>:<integer>', collect the parsed "
            "integer; return all collected integers in input order."
        ),
        edge_cases=(
            "Silently skip any line that lacks a colon or whose value after the colon is "
            "not a valid integer (malformed records are ignored, not fatal). Empty input "
            "yields an empty list. A line of the form ':<integer>' (empty identifier) "
            "still contributes its integer, since only a present colon and a valid trailing "
            "integer are required."
        ),
        output_restriction=(
            "Return only compilable Java source defining class Solution with this method."
        ),
    ),
)


# ===========================================================================
# Markdown canonical repair contract (one instruction accompanying broken_20.md)
# States the required formatting policy WITHOUT revealing the corrected fixture.
# ===========================================================================

MARKDOWN_REPAIR_INSTRUCTION: str = """You are repairing a Markdown document that contains exactly twenty deliberately introduced formatting defects, conventionally labeled MD-01 through MD-20. Do NOT read or rely on any external corrected version; produce the repaired document from the supplied source using only the policy below.

Some of the rules below are this benchmark's canonical styling conventions rather than universal Markdown syntax (for example: forcing a single list marker, forcing one fence-delimiter style, requiring blank lines around lists, mandating spaces-not-tabs, requiring exactly one trailing newline). Treat them as required here even though standard Markdown would allow variation.

Required formatting policy for a valid repaired document:
- MD-01 Heading hierarchy: a heading may advance only ONE level deeper than the immediately previous heading (no skipping levels).
- MD-02 ATX spacing: every '#' heading marker must be followed by exactly one space before its text.
- MD-03 Single H1: the document contains exactly ONE top-level '# ' heading.
- MD-04 Blank-before-heading: every heading is preceded by a blank line, except a heading at the very start of the document.
- MD-05 Consistent bullet markers: within any single unordered list, all items use one consistent marker (-, *, or +).
- MD-06 Sequential numbering: ordered lists start at 1 and increment by exactly 1.
- MD-07 Blank-around-lists: every list is surrounded by a blank line both before and after.
- MD-08 No trailing whitespace: no non-blank line ends with spaces or tabs.
- MD-09 At most one consecutive blank line anywhere in the document.
- MD-10 Link syntax: any bare URL must be wrapped in Markdown link form [text](url).
- MD-11 Fence language: every fenced code block declares a language after its opening fence.
- MD-12 Consistent fence delimiter: all fenced blocks in the document use the SAME delimiter style (all backticks OR all tildes).
- MD-13 Headings not emphasis: no full-line bold/emphasis is used where an ATX heading is expected; use '# ' headings instead.
- MD-14 Unique headings: no two headings share identical normalized text.
- MD-15 No trailing punctuation in headings.
- MD-16 Consistent list indentation: items within one run of a list share a single indentation column.
- MD-17 Table separator: every pipe table includes a header separator row (for example | --- | --- |).
- MD-18 Table columns: every data row in a pipe table matches its header's column count.
- MD-19 Trailing newline: the file uses Unix LF newlines and ends with exactly one trailing newline (no missing final newline, no extra blank line at EOF).
- MD-20 Spaces not tabs: indentation uses spaces, never hard tab characters.

Input: the supplied Markdown document containing these defects.

Requirements for your response:
- Return ONLY the corrected Markdown -- no commentary, no explanations, and no code fences around it.
- Preserve document meaning and body content; change only what the formatting repair requires (including converting bare URLs to link syntax, which may alter visible text).
- Use Unix line endings throughout.

Output only the repaired Markdown document.
"""


# ===========================================================================
# Evidence -- reuse the existing EVID_FIXTURES scenario strings verbatim.
# Each already contains sufficient task/question wording; they are reused as-is.
# ===========================================================================

def evidence_scenarios() -> Dict[str, str]:
    """Return every EVID scenario string verbatim from the frozen suite.

    These scenarios are reused unchanged (no rewrite for style). Each scenario is
    presented to the model and its free-text response is graded on its own, so
    Evidence requires 10 independent live requests -- one per case.
    """
    return {cid: fix.get("scenario", "") for cid, fix in _evidence.EVID_FIXTURES.items()}


# ===========================================================================
# DRIFT-01 -- reuse render_scenario() verbatim (no simplification / rewrite).
# Requires a single live request; the response must be JSON only.
# ===========================================================================

def drift_rendered_scenario(filler: str = "") -> str:
    """Return DRIFT-01's instruction block exactly as ``drift_cases.render_scenario`` builds it."""
    return _drift.render_scenario(filler)


# ===========================================================================
# Live-request topology
# ===========================================================================

def live_request_topology() -> Dict[str, int]:
    """Number of independent live model requests each suite requires.

    python      = 1   (all PYTHON_SPECS combined into one module request)
    java        = len(JAVA_SPECS)   (one per case; solutions collide if merged)
    markdown    = 1   (one full-document repair request -> all defect results)
    evidence    = len(evidence_scenarios())   (one per scenario, graded independently)
    drift       = 1   (one instruction block -> one JSON-only response)
    """
    counts = {
        "python": 1,
        "java": len(JAVA_SPECS),
        "markdown": 1,
        "evidence": len(evidence_scenarios()),
        "drift": 1,
    }
    counts["total"] = sum(counts[k] for k in ("python", "java", "markdown", "evidence", "drift"))
    return counts


EXPECTED_LIVE_REQUESTS: int = live_request_topology()["total"]
