# Task: Implement a lightweight cron-like task scheduler (`cron_lite`)

You are a solo developer building a lightweight cron-like task scheduler
called `cron_lite` for your microservice monitoring setup. Implement
everything in a single Python module using only the standard library
(datetime, dataclasses, threading, typing, enum).

=== Component 1: Cron Expression Parser ===

1. **CronExpression dataclass** (from dataclasses):

   - Fields: minute (str), hour (str), day_of_month (str), month (str),
     day_of_week (str)
   - Each field uses standard cron syntax:
     * `*` matches any valid value
     * `*/N` matches every Nth value (e.g., `*/15` for every 15 minutes)
     * `N-M` matches any value in the range N through M inclusive
     * `N` matches exactly the value N
   - Include a class method `from_string(cls, expr: str) -> CronExpression`
     that splits on whitespace and validates each field has exactly one of the
     four supported formats. Raise ValueError with a descriptive message if
     any field is empty, has too many tokens, or contains invalid numbers.

2. **ScheduledTask dataclass**:

   - Fields:
     * name (str): unique identifier for the task
     * cron (CronExpression): the schedule to follow
     * handler (callable): the function to invoke when the schedule fires
     * args (tuple): positional arguments to pass the handler, default ()
     * enabled (bool): whether the task is active, default True
   - Include `__repr__` that shows name, cron string, and enabled status

3. **Scheduler class**:

   - `__init__()` -- initializes an empty internal list of ScheduledTask
     objects and a threading.Lock for thread safety
   - `add_task(task: ScheduledTask) -> None` -- thread-safe insertion into
     the internal list. If a task with the same name already exists, raise
     ValueError('Task already exists: <name>')
   - `remove_task(name: str) -> bool` -- removes by name, returns True if
     found and removed, False otherwise
   - `enable_task(name: str) -> bool` -- sets enabled=True, returns True
     if found
   - `disable_task(name: str) -> bool` -- sets enabled=False, returns True
     if found
   - `run_now(name: str) -> bool` -- immediately invokes the handler for
     the named task with its stored args. Returns True if found, False
     otherwise
   - `start() -> None` -- launches one background threading.Thread per
     enabled task. Each thread runs `_scheduler_loop` for that task
   - `stop() -> None` -- sets an internal threading.Event to signal all
     threads to exit, then joins each thread with a 5-second timeout
   - `_scheduler_loop(task: ScheduledTask) -> None` -- infinite loop that
     checks every 30 seconds whether the current time matches the task's
     cron expression. On match, calls the handler in a try/except block,
     logging any exceptions to stdout
   - `_is_match(field: str, value: int) -> bool` -- core matching logic:
     * `*` returns True for any value
     * `*/N` returns True when value modulo N equals zero
     * `N-M` returns True when N <= value <= M
     * exact number: converts field to int and compares with value
     * Raises ValueError for unrecognized field formats
   - `_current_cron() -> dict` -- returns a dict mapping field names to
     their current integer values (minute, hour, day_of_month, month,
     day_of_week) using datetime.datetime.now()

4. **Utility function**:

   - `next_run_times(cron: CronExpression, count: int = 5) -> list[datetime]`
     that computes and returns the next *count* datetime objects after the
     current time that would match the cron expression. Iterate minute-by-
     minute from now upward. Do NOT modify the input cron expression.
     Stop once *count* matches are found or after checking 525600 minutes
     (one year) to prevent infinite loops.

=== Component 2: Demo Block ===

The `if __name__ == '__main__':` block must:
1. Parse two cron expressions: `*/5 * * * *` (every 5 minutes) and
   `0 9 * * *` (daily at 9 AM)
2. Create two ScheduledTask objects with simple lambda handlers that
   print a timestamped message
3. Create a Scheduler, add both tasks, start it, sleep for 3 seconds,
   then print all registered task names and their cron strings, and
   finally call stop()
4. Call next_run_times on the first cron expression with count=3 and
   print the results

=== Quality Requirements ===

- All classes and functions must have docstrings
- Type hints on all public methods, properties, and function signatures
- Thread-safe task registration and lifecycle management using threading.Lock
- Proper error handling: ValueError for invalid cron expressions and
  duplicate task names, KeyError for unknown task names
- The `_is_match` method must correctly handle all four cron field formats
  (*, */N, N-M, and exact number)
- Use threading.Timer or threading.Thread for background scheduling (no
  external libraries)
- Code must be PEP 8 compliant with meaningful variable names
- The entire implementation must fit in this single module file

=== Implementation Notes ===

- Use `datetime.datetime.now()` for current time
- Use `datetime.timedelta(minutes=1)` for time arithmetic
- Use `functools.wraps` if wrapping the handler for logging
- Do NOT use the `schedule` or `APScheduler` third-party packages
- Use `threading.Event()` for clean thread shutdown signaling
- Document the cron field validation rules in a module-level docstring