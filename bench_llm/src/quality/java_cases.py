"""BenchLLM V2 Java correctness cases (Act 11C-2B).

Exactly **12** BenchLLM-original cases JAVA-01 .. JAVA-12, each targeting a
meaningfully different Java capability and guarded by several deterministic
assertions.  HumanEval is used only as inspiration for style; no HumanEval
prompt, reference solution or hidden test is copied.

Each case returns structured evidence (see src.quality._common) so the
Results layer can report JAVA-07 FAIL with granular failure type and expected/actual values.

Execution model: canonical subprocess + javac/java via validate_java_solution.
No in-process compilation, no custom classloader sandbox.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from ._common import (CaseDef, CaseResult, Check, COMPILE_ERROR, EXTRACTION_FAILURE,
    RUNTIME_FAILURE, ASSERTION_FAILURE, WRONG_OUTPUT, TIMEOUT)

# ------------------------------------------------------------------
# Inline TestSolution.java fixture template
# Uses single braces since we use .replace() not .format().
# ------------------------------------------------------------------

_TEST_HARNESS_TEMPLATE = """
/**
 * Deterministic test harness for Solution.java.
 * Does NOT use JUnit -- compiles with plain javac.
 * Each test prints PASS/FAIL testName markers and a SUMMARY line.
 */
import java.util.*;

public class TestSolution {

    private static int passed = 0;
    private static int failed = 0;

    private static void check(String testName, boolean condition) {
        if (condition) {
            System.out.println("PASS " + testName);
            passed++;
        } else {
            System.out.println("FAIL " + testName);
            failed++;
        }
    }

    private static void checkDetail(String testName, boolean condition, String expected, String actual) {
        if (condition) {
            System.out.println("PASS " + testName);
            passed++;
        } else {
            System.out.println("FAIL " + testName + " expected=" + expected + " actual=" + actual);
            failed++;
        }
    }

    public static void main(String[] args) {
{test_body}
        System.out.println("SUMMARY " + passed + "/" + (passed + failed));
    }
}
"""

# ------------------------------------------------------------------

# ------------------------------------------------------------------

# ------------------------------------------------------------------

# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Case definitions (JAVA-01 .. JAVA-12)
# ------------------------------------------------------------------

JAVA_CASES: tuple[CaseDef, ...] = (
    CaseDef(
        id="JAVA-01",
        category="java",
        name='Palindrome detection with case normalization',
        capability='strings / character processing',
        checks=(
            Check('empty string', 'is_palindrome', ('',), True),
            Check('single char', 'is_palindrome', ('a',), True),
            Check('even palindrome', 'is_palindrome', ('abba',), True),
            Check('odd palindrome', 'is_palindrome', ('racecar',), True),
            Check('mixed case', 'is_palindrome', ('RaceCar',), True),
            Check('not palindrome', 'is_palindrome', ('hello',), False),
        ),
    ),
    CaseDef(
        id="JAVA-02",
        category="java",
        name='Map frequency grouping by first letter',
        capability='collections / Map counting',
        checks=(
            Check('lowercase words', 'groupByFirstLetter', (['apple', 'ant', 'bat'],), {'a': 2, 'b': 1}),
            Check('mixed case lowercased', 'groupByFirstLetter', (['Hello', 'World'],), {'h': 1, 'w': 1}),
            Check('empty input', 'groupByFirstLetter', ([],), {}),
            Check('more entries', 'groupByFirstLetter', (['Cat', 'dog', 'Duck'],), {'c': 1, 'd': 2}),
        ),
    ),
    CaseDef(
        id="JAVA-03",
        category="java",
        name='Enum severity classification with fallback',
        capability='enum / string mapping',
        checks=(
            Check('debug code', 'classifySeverity', ('D',), 'DEBUG'),
            Check('warn code', 'classifySeverity', ('W',), 'WARN'),
            Check('error code', 'classifySeverity', ('E',), 'ERROR'),
            Check('info code', 'classifySeverity', ('I',), 'INFO'),
            Check('unknown code', 'classifySeverity', ('X',), 'UNKNOWN'),
        ),
    ),
    CaseDef(
        id="JAVA-04",
        category="java",
        name='Null-safe string processing',
        capability='null handling / defensive coding',
        checks=(
            Check('null input', 'safe_length', (None,), 0),
            Check('empty string', 'safe_length', ('',), 0),
            Check('normal string', 'safe_length', ('hello',), 5),
            Check('whitespace only', 'safe_length', ('   ',), 3),
        ),
    ),
    CaseDef(
        id="JAVA-05",
        category="java",
        name='Safe division with custom exception handling',
        capability='exception handling / error propagation',
        checks=(
            Check('normal division', 'safe_divide', (10, 2), 5.0),
            Check('zero divisor', 'safe_divide', (10, 0), None),
            Check('negative dividend', 'safe_divide', (-10, 3), -3.3333333333333335),
            Check('both zero', 'safe_divide', (0, 0), None),
        ),
    ),
    CaseDef(
        id="JAVA-06",
        category="java",
        name='Point class with distance calculation',
        capability='classes / small data structure',
        checks=(
            Check('origin to origin', 'distance', (0.0, 0.0, 0.0, 0.0), 0.0),
            Check('axis aligned', 'distance', (0.0, 0.0, 3.0, 4.0), 5.0),
            Check('asymmetric coords', 'distance', (-1.0, 0.0, 1.0, 0.0), 2.0),
            Check('negative coords', 'distance', (-1.0, -1.0, 1.0, 1.0), 2.8284271247461903),
        ),
    ),
    CaseDef(
        id="JAVA-07",
        category="java",
        name='Comparator stable sort by length then lexicographic',
        capability='sorting / Comparator tie-break',
        checks=(
            Check('different lengths', 'sortByLengthThenLexicographic', (['banana', 'pie', 'apple'],), ['pie', 'apple', 'banana']),
            Check('all same length lex', 'sortByLengthThenLexicographic', (['cat', 'dog', 'ant', 'emu'],), ['ant', 'cat', 'dog', 'emu']),
            Check('ties keep input order', 'sortByLengthThenLexicographic', (['a', 'bb', 'cc'],), ['a', 'bb', 'cc']),
            Check('empty input', 'sortByLengthThenLexicographic', ([],), []),
        ),
    ),
    CaseDef(
        id="JAVA-08",
        category="java",
        name='Nested list flattening',
        capability='nested collection transformation',
        checks=(
            Check('multi-row', 'flattenNested', ([[1, 2], [3], [4, 5]],), [1, 2, 3, 4, 5]),
            Check('empty matrix', 'flattenNested', ([],), []),
            Check('empty rows', 'flattenNested', ([[]],), []),
            Check('mixed empties', 'flattenNested', ([[], [], [7]],), [7]),
        ),
    ),
    CaseDef(
        id="JAVA-09",
        category="java",
        name='Stateful counter update/reset behaviour',
        capability='stateful class update/reset',
        checks=(
            Check('sequence with reset', 'runOperations', (['inc', 'inc', 'dec', 'inc'],), 2),
            Check('reset then negative', 'runOperations', (['inc', 'inc', 'reset', 'dec'],), -1),
            Check('no ops', 'runOperations', ([],), 0),
            Check('unknown ops ignored', 'runOperations', (['unknown', 'foo'],), 0),
        ),
    ),
    CaseDef(
        id="JAVA-10",
        category="java",
        name='Enum weekday ordinal arithmetic',
        capability='enum / string mapping',
        checks=(
            Check('monday to tuesday', 'daysBetween', ('MONDAY', 'TUESDAY'), 1),
            Check('friday to monday', 'daysBetween', ('FRIDAY', 'MONDAY'), 3),
            Check('sunday to monday', 'daysBetween', ('SUNDAY', 'MONDAY'), 1),
            Check('tuesday to wednesday', 'daysBetween', ('TUESDAY', 'WEDNESDAY'), 1),
            Check('invalid weekday', 'daysBetween', ('MONDAY', 'GARBAGE'), -1),
        ),
    ),
    CaseDef(
        id="JAVA-11",
        category="java",
        name='Immutable Range value-object containment',
        capability='immutable / value-object logic, null-safe',
        checks=(
            Check('inclusive low boundary', 'allWithin', ([0], 0, 5), True),
            Check('above high boundary', 'allWithin', ([6], 0, 5), False),
            Check('multiple in range', 'allWithin', ([1, 2, 3, 4, 5], 0, 5), True),
            Check('empty list vacuous true', 'allWithin', ([], 0, 5), True),
        ),
    ),
    CaseDef(
        id="JAVA-12",
        category="java",
        name='Exception-based record parsing',
        capability='exception / error handling',
        checks=(
            Check('all valid', 'parseRecords', (['a:1', 'b:2', 'c:3'],), [1, 2, 3]),
            Check('skip malformed', 'parseRecords', (['x:5', 'bad', 'y:9'],), [5, 9]),
            Check('empty input', 'parseRecords', ([],), []),
            Check('partial valid', 'parseRecords', (['no_colon', ':7'],), [7]),
        ),
    ),
)

# ------------------------------------------------------------------
# Reference solutions -- one Solution.java per case
# ------------------------------------------------------------------

REFERENCE_SOLUTIONS: dict[str, str] = {
    'JAVA-01': r"""public class Solution {
    public static boolean is_palindrome(String s) {
        if (s == null || s.isEmpty()) return true;
        String lower = s.toLowerCase();
        int left = 0, right = lower.length() - 1;
        while (left < right) {
            if (lower.charAt(left) != lower.charAt(right)) return false;
            left++;
            right--;
        }
        return true;
    }
}""",
    'JAVA-02': r"""import java.util.*;

public class Solution {
    public static Map<String, Integer> groupByFirstLetter(List<String> words) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (String w : words) {
            if (w == null || w.isEmpty()) continue;
            String key = String.valueOf(Character.toLowerCase(w.charAt(0)));
            counts.merge(key, 1, Integer::sum);
        }
        return counts;
    }
}""",
    'JAVA-03': r"""import java.util.*;

public class Solution {
    private enum Severity { DEBUG, INFO, WARN, ERROR }

    private static final Map<String, Severity> CODES = new HashMap<>();
    static {
        CODES.put("D", Severity.DEBUG);
        CODES.put("I", Severity.INFO);
        CODES.put("W", Severity.WARN);
        CODES.put("E", Severity.ERROR);
    }

    public static String classifySeverity(String code) {
        Severity s = CODES.get(code);
        return s == null ? "UNKNOWN" : s.name();
    }
}""",
    'JAVA-04': r"""public class Solution {
    public static int safe_length(String s) {
        if (s == null) return 0;
        return s.length();
    }
}""",
    'JAVA-05': r"""public class Solution {
    public static Double safe_divide(double a, double b) {
        if (b == 0.0) return null;
        return a / b;
    }
}""",
    'JAVA-06': r"""public class Solution {
    public static double distance(double x1, double y1, double x2, double y2) {
        double dx = x2 - x1;
        double dy = y2 - y1;
        return Math.sqrt(dx * dx + dy * dy);
    }
}""",
    'JAVA-07': r"""import java.util.*;

public class Solution {
    public static List<String> sortByLengthThenLexicographic(List<String> words) {
        List<String> out = new ArrayList<>(words);
        out.sort(Comparator.comparingInt(String::length).thenComparing(Comparator.naturalOrder()));
        return out;
    }
}""",
    'JAVA-08': r"""import java.util.*;

public class Solution {
    public static List<Integer> flattenNested(List<List<Integer>> matrix) {
        List<Integer> out = new ArrayList<>();
        for (List<Integer> row : matrix) {
            if (row == null) continue;
            out.addAll(row);
        }
        return out;
    }
}""",
    'JAVA-09': r"""import java.util.*;

public class Solution {
    public static int runOperations(List<String> ops) {
        int count = 0;
        for (String op : ops) {
            if (op.equals("inc")) {
                count += 1;
            } else if (op.equals("dec")) {
                count -= 1;
            } else if (op.equals("reset")) {
                count = 0;
            }
        }
        return count;
    }
}""",
    'JAVA-10': r"""public class Solution {
    private enum Weekday { MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY }

    public static int daysBetween(String from, String to) {
        try {
            Weekday a = Weekday.valueOf(from);
            Weekday b = Weekday.valueOf(to);
            return (b.ordinal() - a.ordinal() + 7) % 7;
        } catch (IllegalArgumentException e) {
            return -1;
        }
    }
}""",
    'JAVA-11': r"""import java.util.*;

public class Solution {
    private static final class Range {
        final int low;
        final int high;
        Range(int low, int high) {
            this.low = low;
            this.high = high;
        }
        boolean contains(int x) {
            return x >= low && x <= high;
        }
    }

    public static boolean allWithin(List<Integer> values, int lo, int hi) {
        Range r = new Range(lo, hi);
        for (int v : values) {
            if (!r.contains(v)) return false;
        }
        return true;
    }
}""",
    'JAVA-12': r"""import java.util.*;

public class Solution {
    public static List<Integer> parseRecords(List<String> lines) {
        List<Integer> out = new ArrayList<>();
        for (String line : lines) {
            try {
                String[] parts = line.split(":");
                if (parts.length < 2) throw new IllegalArgumentException("bad format");
                int val = Integer.parseInt(parts[1].trim());
                out.add(val);
            } catch (RuntimeException e) {
            }
        }
        return out;
    }
}""",
}

# Test harness builder  generates TestSolution.java for a case
# ------------------------------------------------------------------

def _build_test_harness(case):
    """Build a TestSolution.java test harness for *case*.

    Each Check becomes a call to Solution.<func>(<args>) with an assertion.
    Uses checkDetail() for granular expected/actual output on failure.
    """
    test_body_lines = []

    for idx, check in enumerate(case.checks, start=1):
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', check.func)
        args_str = _java_args(check.args)
        expected_str = _java_expected(check.expected)

        test_body_lines.append('        // Check ' + str(idx) + ': ' + check.label)

        if isinstance(check.expected, bool):
            val = 'true' if check.expected else 'false'
            test_body_lines.append(
                '        check("test_' + safe_name + '_' + format(idx, "02d") + '", '
                'Solution.' + check.func + '(' + args_str + ') == ' + val + ');')
        elif isinstance(check.expected, (int, float)):
            test_body_lines.append(
                '        {double result = Solution.' + check.func + '(' + args_str + ');'
                'boolean ok = Double.compare(result, ' + expected_str + ') == 0;'
                'checkDetail("test_' + safe_name + '_' + format(idx, "02d") + '", ok,'
                '    String.valueOf(' + expected_str + '), '
                '    String.valueOf(result));}')
        elif isinstance(check.expected, str):
            escaped = check.expected.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))
            test_body_lines.append(
                '        {String result = Solution.' + check.func + '(' + args_str + ');'
                'boolean ok = "' + escaped + '".equals(result);'
                'checkDetail("test_' + safe_name + '_' + format(idx, "02d") + '", ok,'
                '    "' + escaped + '",'
                '    "\\\\" + result + "\\\\");}')
        elif check.expected is None:
            test_body_lines.append(
                '        check("test_' + safe_name + '_' + format(idx, "02d") + '", '
                'Solution.' + check.func + '(' + args_str + ') == null);')
        elif isinstance(check.expected, list):
            java_list = _java_list_of(check.expected)
            display_list = _java_list_display(check.expected)
            test_body_lines.append(
                '        {var result = Solution.' + check.func + '(' + args_str + ');'
                'boolean ok = ' + java_list + '.equals(result);'
                'checkDetail("test_' + safe_name + '_' + format(idx, "02d") + '", ok,'
                '    "' + display_list + '",'
                '    String.valueOf(result));}')
        elif isinstance(check.expected, dict):
            java_map = _java_map_literal(check.expected)
            display_map = _java_map_display(check.expected)
            test_body_lines.append(
                '        {var result = Solution.' + check.func + '(' + args_str + ');'
                'boolean ok = ' + java_map + '.equals(result);'
                'checkDetail("test_' + safe_name + '_' + format(idx, "02d") + '", ok,'
                '    "' + display_map + '",'
                '    String.valueOf(result));}')

        test_body_lines.append('')  # blank line between checks

    harness = _TEST_HARNESS_TEMPLATE.replace('{test_body}', chr(10).join(test_body_lines))
    return harness


def _java_args(args):
    """Format Java method arguments for generated test code.
    
    Handles both flat args (int, str, etc.) and wrapped list args.
    When an arg element is a tuple/list containing primitives, it's treated as a single List argument.
    When an arg element is a primitive, it's passed directly.
    """
    parts = []
    for a in args:
        if isinstance(a, bool):
            parts.append('Boolean.valueOf(' + str(a).lower() + ')')
        elif isinstance(a, int) and not isinstance(a, bool):
            parts.append(str(a))
        elif isinstance(a, float):
            parts.append(str(a) + 'd')
        elif isinstance(a, str):
            escaped = a.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))
            parts.append('"' + escaped + '"')
        elif isinstance(a, list):
            parts.append(_java_list_of(a))
        elif isinstance(a, tuple) and len(a) > 0:
            # Check if this tuple contains primitives (was originally a single list arg that got serialized as tuple)
            all_primitives = all(isinstance(x, (int, float, str, bool)) or x is None for x in a)
            if all_primitives:
                # This was originally a single list argument  wrap in List.of()
                parts.append(_java_list_of(list(a)))
            else:
                # Nested structure  convert recursively
                items = []
                for item in a:
                    if isinstance(item, (int, float)):
                        items.append(str(item))
                    elif isinstance(item, str):
                        escaped = item.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))
                        items.append('"' + escaped + '"')
                    elif item is None:
                        items.append('null')
                    else:
                        items.append(str(item))
                parts.append('(' + ', '.join(items) + ')')
        elif a is None:
            parts.append('null')
        else:
            parts.append(str(a))
    return ', '.join(parts)


def _java_expected(value):
    """Format expected value for Java Double.compare comparison."""
    if isinstance(value, int):
        return str(value) + 'd'
    elif isinstance(value, float):
        return str(value) + 'd'
    else:
        return str(value)


def _java_list_of(lst):
    """Convert a Python list to a Java List.of() literal."""
    items = []
    for item in lst:
        if isinstance(item, int):
            items.append('Integer.valueOf(' + str(item) + ')')
        elif isinstance(item, float):
            items.append('Double.valueOf(' + str(item) + 'd)')
        elif isinstance(item, str):
            escaped = item.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))
            items.append('"' + escaped + '"')
        elif isinstance(item, list):
            items.append(_java_list_of(item))
    return 'List.of(' + ', '.join(items) + ')'


def _java_list_display(lst):
    """Convert a Python list to a simple display string, escaped for Java strings."""
    items = []
    for item in lst:
        if isinstance(item, int):
            items.append(str(item))
        elif isinstance(item, float):
            items.append(str(item))
        elif isinstance(item, str):
            # Escape quotes for embedding in Java string literal
            escaped = item.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))
            items.append('\\\\\\"' + escaped + '\\\\\\"')
    return '[' + ', '.join(items) + ']'


def _java_map_literal(d):
    """Convert a Python dict to a Java Map.of() literal.

    Java Map.of() takes alternating key-value pairs, not key->value syntax.
    """
    parts = []
    for k, v in d.items():
        if isinstance(v, list):
            items = ', '.join(str(x) for x in v)
            parts.append('"' + k + '"')
            parts.append('List.of(' + items + ')')
        else:
            parts.append('"' + k + '"')
            parts.append(str(v))
    return 'Map.of(' + ', '.join(parts) + ')'

def _java_map_display(d):
    """Convert a Python dict to a simple display string."""
    parts = []
    for k, v in d.items():
        if isinstance(v, list):
            items = ', '.join(str(x) for x in v)
            parts.append(k + '=' + '[' + items + ']')
        else:
            parts.append(k + '=' + str(v))
    return '{' + ', '.join(parts) + '}'


# ------------------------------------------------------------------
# Subprocess + javac/java runner (canonical model  no in-process exec)
# ------------------------------------------------------------------

def _run_case_test(case, generated_code):
    """Run the canonical subprocess/javac validator for a single case."""
    import subprocess

    tmpdir = tempfile.mkdtemp(prefix='benchllm_java_case_')
    try:
        sol_path = Path(tmpdir) / 'Solution.java'
        sol_path.write_text(generated_code, encoding='utf-8')

        test_harness = _build_test_harness(case)
        test_path = Path(tmpdir) / 'TestSolution.java'
        test_path.write_text(test_harness, encoding='utf-8')

        compile_proc = subprocess.run(
            ['javac', 'Solution.java', 'TestSolution.java'],
            cwd=tmpdir, capture_output=True, text=True, timeout=30.0)

        if compile_proc.returncode != 0:
            return [CaseResult(case_id=case.id, category=case.category, name=case.name,
                passed=False, checks_passed=0, checks_total=len(case.checks),
                failure_type='compile_error',
                failure_reason='javac error: ' + compile_proc.stderr[:200])
                for _ in case.checks]

        run_proc = subprocess.run(
            ['java', 'TestSolution'],
            cwd=tmpdir, capture_output=True, text=True, timeout=15.0)

        stdout = run_proc.stdout or ''
        test_results = _parse_java_output(stdout)

        results = []
        for check in case.checks:
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', check.func)
            test_name = 'test_' + safe_name + '_' + format(case.checks.index(check) + 1, '02d')

            matched = None
            for tr in test_results:
                if tr['name'] == test_name:
                    matched = tr
                    break

            if matched is None:
                results.append(CaseResult(case_id=case.id, category=case.category, name=case.name,
                    passed=False, checks_passed=0, checks_total=len(case.checks),
                    failure_type='extraction_failure',
                    failure_reason='Test ' + test_name + ' was not collected'))
            elif matched['passed']:
                results.append(CaseResult(case_id=case.id, category=case.category, name=case.name,
                    passed=True, checks_passed=1, checks_total=1))
            else:
                check_result = _parse_java_failure(check, matched.get('error'))
                results.append(CaseResult(case_id=case.id, category=case.category, name=case.name,
                    passed=False, checks_passed=0, checks_total=1,
                    failure_type=check_result['failure_type'],
                    failure_reason=check_result['reason'],
                    expected=check_result.get('expected', ''),
                    actual=check_result.get('actual', '')))

        return results

    except subprocess.TimeoutExpired:
        return [CaseResult(case_id=case.id, category=case.category, name=case.name,
            passed=False, checks_passed=0, checks_total=len(case.checks),
            failure_type='timeout',
            failure_reason='TestSolution execution timed out (15s)')
            for _ in case.checks]

    except Exception as exc:
        return [CaseResult(case_id=case.id, category=case.category, name=case.name,
            passed=False, checks_passed=0, checks_total=len(case.checks),
            failure_type='runtime_failure',
            failure_reason='Unexpected error: ' + type(exc).__name__ + ': ' + str(exc))
            for _ in case.checks]

    finally:
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass


def _parse_java_output(output):
    """Parse TestSolution.java PASS/FAIL output for per-test results."""
    results = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith('PASS '):
            test_name = stripped[5:].strip()
            results.append({'name': test_name, 'passed': True, 'error': None})
        elif stripped.startswith('FAIL '):
            rest = stripped[5:].strip()
            m = re.match(r'(\S+)\s+expected=(.+?)\s+actual=(.*)', rest)
            if m:
                test_name = m.group(1)
                error_detail = 'expected=' + m.group(2) + ' actual=' + m.group(3).strip()
                results.append({'name': test_name, 'passed': False, 'error': error_detail})
            else:
                test_name = rest.strip()
                results.append({'name': test_name, 'passed': False, 'error': None})
    return results


def _parse_java_failure(check, error):
    """Parse a Java test failure into structured fields."""
    expected_str = ''
    actual_str = ''

    if error:
        m_exp = re.search(r'expected=(.+?)\s+actual=', str(error))
        if m_exp:
            expected_str = m_exp.group(1).strip()
        m_act = re.search(r'actual=(.*)', str(error))
        if m_act:
            actual_str = m_act.group(1).strip()

    failure_type = 'assertion_failure'
    if error and ('NullPointerException' in str(error) or 'NumberFormatException' in str(error)):
        failure_type = 'runtime_failure'

    return {
        'failure_type': failure_type,
        'reason': 'Solution.' + check.func + '(' + ', '.join(repr(a) for a in check.args) + ') failed',
        'expected': expected_str or _fmt_java(check.expected),
        'actual': actual_str,
    }


def _fmt_java(value):
    """Format a value as a Java-compatible string representation."""
    if isinstance(value, bool):
        return str(value).lower()
    elif value is None:
        return 'null'
    else:
        return str(value)


# ------------------------------------------------------------------
# Public API  validate() uses canonical subprocess/javac path
# ------------------------------------------------------------------

def validate(source):
    """Validate a submitted Java module *source* against all JAVA cases."""
    results = []
    for case in JAVA_CASES:
        try:
            case_results = _run_case_test(case, source)
            results.extend(case_results)
        except Exception as exc:
            results.extend([CaseResult(case_id=case.id, category=case.category, name=case.name,
                passed=False, checks_passed=0, checks_total=len(case.checks),
                failure_type='runtime_failure',
                failure_reason='Unexpected error: ' + type(exc).__name__ + ': ' + str(exc))
                for _ in case.checks])
    return results


def all_function_names():
    """All function names referenced across the JAVA cases."""
    return {c.func for case in JAVA_CASES for c in case.checks}
