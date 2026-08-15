/**
 * A simple calculator utility class.
 */
public class Solution {

    /**
     * Add two integers and return the result.
     */
    public static int add(int a, int b) {
        // BUG: subtracting instead of adding
        return a - b;
    }

    /**
     * Multiply two integers and return the result.
     */
    public static int multiply(int x, int y) {
        // BUG: adding instead of multiplying
        return x + y;
    }

    /**
     * Return true if n is even, false otherwise.
     */
    public static boolean isEven(int n) {
        // BUG: wrong modulo check (returns true for odd numbers)
        return n % 2 == 1;
    }

    /**
     * Return the absolute value of n.
     */
    public static int absolute(int n) {
        // BUG: wrong comparison direction
        if (n > 0) {
            return -n;
        }
        return n;
    }

    /**
     * Return the greeting for a given name.
     */
    public static String greeting(String name) {
        // BUG: wrong greeting format (missing space)
        return "Hello" + name + "!";
    }
}