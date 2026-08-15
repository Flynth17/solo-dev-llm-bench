/**
 * Deterministic test harness for Solution.java.
 * Does NOT use JUnit — compiles with plain javac.
 * Each test prints PASS/FAIL testName.
 * Prints SUMMARY passed/total at the end.
 */
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

    public static void main(String[] args) {
        // Test 1: add(2, 3) == 5
        check("testAdd", Solution.add(2, 3) == 5);

        // Test 2: multiply(4, 5) == 20
        check("testMultiply", Solution.multiply(4, 5) == 20);

        // Test 3: isEven(4) == true
        check("testIsEvenTrue", Solution.isEven(4) == true);

        // Test 4: isEven(3) == false
        check("testIsEvenFalse", Solution.isEven(3) == false);

        // Test 5: absolute(-5) == 5
        check("testAbsoluteNegative", Solution.absolute(-5) == 5);

        // Test 6: absolute(5) == 5
        check("testAbsolutePositive", Solution.absolute(5) == 5);

        // Test 7: greeting("World") == "Hello World!"
        check("testGreeting", "Hello World!".equals(Solution.greeting("World")));

        System.out.println("SUMMARY " + passed + "/" + (passed + failed));
    }
}