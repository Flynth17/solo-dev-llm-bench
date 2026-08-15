# Task: Design a lightweight distributed task queue system (`mini_queue`)

You are designing a lightweight distributed task queue system for a solo
developer who needs something between a simple in-process queue and a full
Redis-backed Celery setup. The system must handle background job processing
for a microservice that sends emails, generates PDF reports, processes
payments, and runs analytics. Implement everything in a single Python module
called `mini_queue` using only the Python standard library
(threading, queue, json, uuid, datetime, logging, dataclasses, enum,
typing, http.server, contextlib, urllib.parse).

=== Architecture Overview ===

The system consists of four major layers:
1. **Task model layer** -- serializable job descriptions with status tracking
2. **Queue backend layer** -- in-memory priority queue with thread safety
3. **Worker pool layer** -- threaded consumers that process tasks
4. **HTTP API layer** -- optional REST-like interface for remote task submission

=== Component 1: Task Model ===

1. **TaskStatus** (enum.IntEnum):

   Define the following statuses with these integer values:
   - PENDING = 0
   - QUEUED = 1
   - RUNNING = 2
   - COMPLETED = 3
   - FAILED = 4
   - RETRY = 5
   Include a docstring explaining that this is an IntEnum so it can be
   serialized to integers in JSON. Document what each status means in terms
   of the task lifecycle.

2. **TaskRecord** (dataclass):

   Fields with detailed type annotations:
   - task_id (str): UUID4-generated unique identifier, set in __post_init__
   - name (str): logical task name (e.g., 'email_send', 'pdf_generate')
   - payload (dict): JSON-serializable input data passed to the handler
   - status (TaskStatus): current status, defaults to TaskStatus.PENDING
   - priority (int): 1=lowest to 10=highest, default 5
   - result (dict | None): output data after successful completion, default None
   - error (str | None): error message if FAILED, default None
   - created_at (str): ISO format timestamp from datetime.datetime.utcnow
   - updated_at (str): ISO format timestamp, updated on each status change
   - attempts (int): number of processing attempts, default 0
   - max_retries (int): maximum retry attempts before permanent failure,
     default 3
   - worker (str | None): name of the worker that last processed it,
     default None

   Methods (each with docstring and type hints):
   - `to_dict() -> dict`: serialize all fields to a plain dict suitable
     for json.dumps. Must NOT include any non-serializable objects.
   - `from_dict(data: dict) -> TaskRecord`: class method that deserializes
     from a dict. Must convert status back to TaskStatus enum. Raise KeyError
     if required fields are missing.
   - `mark_running(worker: str) -> None`: set status to RUNNING, update
     worker name and updated_at timestamp
   - `mark_completed(result: dict) -> None`: set status to COMPLETED,
     store result dict, update updated_at. Raise RuntimeError if status is
     not RUNNING.
   - `mark_failed(error: str) -> None`: set status to FAILED, store error
     message, increment attempts, update updated_at
   - `should_retry() -> bool`: return True if attempts < max_retries

3. **TaskRecord validation**:

   In `__post_init__`, validate that:
   - priority is between 1 and 10 inclusive, raise ValueError otherwise
   - name is a non-empty string, raise ValueError otherwise
   - payload is a dict, raise TypeError otherwise
   Generate task_id using uuid.uuid4().hex if not provided
   Set created_at and updated_at using datetime.datetime.utcnow().isoformat()

=== Component 2: Queue Backend ===

4. **PriorityQueueBackend** class:

   Internal state:
   - `_tasks`: dict mapping task_id (str) to TaskRecord
   - `_lock`: threading.Lock for thread-safe operations
   - `_condition`: threading.Condition(_lock) for blocking dequeue
   - `_queue_order`: list of task_ids in priority order (high priority first)

   Methods:
   - `__init__()`: initialize all internal state
   - `enqueue(task: TaskRecord) -> str`:
     - Validate task status is PENDING, raise ValueError otherwise
     - Set task status to QUEUED, update updated_at
     - Insert task_id into _queue_order maintaining priority sort (highest first)
     - Notify one waiting thread via _condition.notify_one()
     - Return task.task_id
   - `dequeue() -> TaskRecord | None`:
     - Acquire _lock
     - While _queue_order is empty AND no shutdown signal, call _condition.wait(timeout=1.0)
     - If _queue_order is empty after waking, return None
     - Pop highest priority task_id from _queue_order
     - Look up TaskRecord, verify status is QUEUED
     - Return the task (caller must mark it RUNNING)
   - `get_task(task_id: str) -> TaskRecord`:
     - Acquire _lock, look up task_id in _tasks
     - Raise KeyError(f'Task {task_id} not found') if missing
   - `list_tasks(status: TaskStatus | None = None) -> list[TaskRecord]`:
     - Acquire _lock
     - If status is None, return all tasks sorted by priority desc then created_at
     - Otherwise filter by status, same sorting
     - Return a copy list to avoid external mutation
   - `retry_task(task_id: str) -> bool`:
     - Acquire _lock
     - Get task, if status is not FAILED return False
     - If task.should_retry(), set status back to PENDING, re-enqueue
     - Return True if retried, False otherwise
   - `stats() -> dict`:
     - Acquire _lock
     - Return {status.name: count} for each TaskStatus value
     - Include a '_total' key with total task count

=== Component 3: Worker Pool ===

5. **HandlerRegistry** class:

   Internal state:
   - `_handlers`: dict mapping task_name (str) to callable handler
   - `_lock`: threading.Lock

   Methods:
   - `register(task_name: str, handler: callable) -> None`:
     - Validate task_name is non-empty string
     - Store handler under task_name, raise ValueError if duplicate
   - `get_handler(task_name: str) -> callable`:
     - Return registered handler, raise KeyError if not found
   - `list_handlers() -> dict[str, callable]`:
     - Return a shallow copy of the internal handlers dict
   - `has_handler(task_name: str) -> bool`:
     - Return True if handler is registered for task_name

6. **Worker** class:

   Internal state:
   - `_name`: str -- unique worker identifier (e.g., 'worker-0')
   - `_backend`: PriorityQueueBackend reference
   - `_registry`: HandlerRegistry reference
   - `_logger`: logging.Logger configured for this worker
   - `_running`: threading.Event -- True while the worker is active
   - `_thread`: threading.Thread | None -- the processing thread

   Methods:
   - `__init__(name: str, backend: PriorityQueueBackend, registry: HandlerRegistry)`:
     - Validate name is non-empty
     - Set up logging: logger = logging.getLogger(f'worker.{name}'),
       set level to logging.INFO, add a StreamHandler with format
       '[%(name)s] %(levelname)s: %(message)s'
     - Store backend and registry references
     - Initialize _running to False, _thread to None
   - `start() -> None`:
     - If _running is True, return (already started)
     - Set _running to True
     - Create and start a new threading.Thread targeting _process_loop
     - Store thread reference in _thread
     - Log 'Worker started'
   - `stop() -> None`:
     - Set _running to False
     - Notify _condition on backend to wake any blocked dequeue
     - If _thread is not None, join with timeout=10 seconds
     - Log 'Worker stopped'
   - `_process_loop() -> None`:
     - Main processing loop: while self._running:
       - Call task = self._backend.dequeue()
       - If task is None, continue (no tasks available)
       - Call self._handle_task(task)
   - `_handle_task(task: TaskRecord) -> None`:
     - Increment task.attempts
     - Mark task as RUNNING with worker name
     - Log 'Processing task {task_id} ({name}, attempt {attempts})'
     - Try:
       - Get handler from registry for task.name
       - Call handler(task.payload)
       - Mark task as COMPLETED with {"output": result}
       - Log 'Task {task_id} completed successfully'
     - Except Exception as exc:
       - Mark task as FAILED with str(exc)
       - If task.should_retry():
         - Set task status to RETRY
         - Log 'Task {task_id} marked for retry ({attempts}/{max_retries})'
       - Else:
         - Log 'Task {task_id} permanently failed: {error}'

7. **TaskQueue** facade class:

   Internal state:
   - `_backend`: PriorityQueueBackend
   - `_registry`: HandlerRegistry
   - `_workers`: list[Worker]
   - `_lock`: threading.Lock for worker list management

   Methods:
   - `__init__()`: create backend and registry, initialize empty workers list
   - `submit(name: str, payload: dict, priority: int = 5) -> str`:
     - Create a TaskRecord with the given name, payload, and priority
     - Enqueue the task, return task_id
   - `register_handler(task_name: str, handler: callable) -> None`:
     - Register a handler in the registry
   - `create_worker(name: str) -> Worker`:
     - Create a Worker instance but do NOT start it
   - `start_workers(count: int = 3) -> list[Worker]`:
     - Create and start *count* workers with names 'worker-0', 'worker-1', etc.
     - Store in _workers list under lock protection
     - Return the list of started workers
   - `get_result(task_id: str) -> dict | None`:
     - Get task from backend, if status is COMPLETED return result dict
     - Return None if task not found or not completed
   - `get_stats() -> dict`: delegate to backend.stats()
   - `get_task_status(task_id: str) -> TaskStatus`:
     - Get task and return its current status
     - Raise KeyError if task not found
   - `shutdown() -> None`:
     - Acquire _lock, stop all workers, clear _workers list
   - `retry_failed(task_id: str) -> bool`:
     - Delegate to backend.retry_task(task_id)

=== Component 4: HTTP API ===

8. **APIHandler** (inherits http.server.BaseHTTPRequestHandler):

   Class attribute:
   - `task_queue: TaskQueue` -- set by create_app before serving

   Methods:
   - `__init__(self, request, client_address, server)`: call super().__init__
   - `log_message(format, *args) -> None`: override to suppress default logging
   - `_get_body() -> bytes`: read Content-Length header, read and return body
   - `send_json(status_code: int, data: dict) -> None`:
     - Set response headers: Content-Type to application/json,
       Content-Length to len of encoded body
     - Write json.dumps(data) encoded as UTF-8
   - `do_GET() -> None`:
     - Parse path using urllib.parse.urlparse
     - If path == '/stats': get stats from task_queue, send 200 with JSON
     - If path starts with '/tasks/': extract task_id, get result,
       send 200 with result dict or 404 if not found
     - Else: send 404 with error message
   - `do_POST() -> None`:
     - Parse path using urllib.parse.urlparse
     - If path == '/submit': read body, parse JSON, extract name/payload/priority,
       submit to task_queue, send 201 with {task_id, status}
     - If body is invalid JSON: send 400 with error message
     - Else: send 404 with error message

9. **create_app(task_queue: TaskQueue, host: str, port: int) -> None**:

   - Set APIHandler.task_queue = task_queue
   - Create http.server.HTTPServer((host, port), APIHandler)
   - Print 'MiniQueue server running on http://{host}:{port}'
   - Call server.serve_forever()

=== Component 5: Persistence Layer ===

10. **TaskSerializer** class:

    - `serialize(task: TaskRecord) -> str`: convert a TaskRecord to a JSON
      string. Use task.to_dict() first. Handle any serialization errors by
      wrapping them in a RuntimeError.
    - `deserialize(json_str: str) -> TaskRecord`: parse JSON string and
      create a TaskRecord using TaskRecord.from_dict(). Handle any parsing
      errors by wrapping them in a ValueError.
    - `serialize_list(tasks: list[TaskRecord]) -> str`: serialize a list
      of TaskRecords to a JSON array string.
    - `deserialize_list(json_str: str) -> list[TaskRecord]`: deserialize
      a JSON array string to a list of TaskRecords.

11. **FileStore** class for persistent storage:

    - `__init__(filepath: str)`: initialize with a file path. Create the
      file if it does not exist. Load existing tasks from the file if present.
    - `_load() -> None`: private method that reads the file and deserializes
      tasks into an internal dict. If the file is empty or invalid JSON,
      initialize with an empty dict.
    - `_save() -> None`: private method that serializes all tasks to JSON
      and writes to the file atomically (write to temp file then rename).
    - `save_task(task: TaskRecord) -> None`: update internal state, call _save().
      Use threading.Lock for thread safety.
    - `load_task(task_id: str) -> TaskRecord | None`: return the TaskRecord
      for task_id, or None if not found.
    - `load_all() -> list[TaskRecord]`: return all stored tasks.
    - `load_by_status(status: TaskStatus) -> list[TaskRecord]`: filter and
      return tasks matching the given status.
    - `delete_task(task_id: str) -> bool`: remove a task by ID, return True
      if found and deleted, False otherwise.
    - `clear() -> None`: remove all tasks from storage.
    - `count() -> int`: return the number of stored tasks.

=== Component 6: Metrics Collector ===

12. **MetricsCollector** class:

    Internal state:
    - `_task_count`: dict mapping task_name to total submission count
    - `_status_count`: dict mapping TaskStatus name to current count
    - `_latency_samples`: list of float durations in seconds
    - `_worker_tasks`: dict mapping worker name to task completion count
    - `_lock`: threading.Lock
    - `_start_time`: datetime.datetime when the collector was created

    Methods:
    - `__init__()`: initialize all counters, set _start_time to now
    - `record_submission(task_name: str) -> None`: increment task_count for
      task_name
    - `record_status_change(status: TaskStatus) -> None`: increment status_count
      for the status name
    - `record_latency(duration_seconds: float) -> None`: append to latency_samples
    - `record_worker_completion(worker: str) -> None`: increment worker_tasks for
      the worker name
    - `get_summary() -> dict`: return a dict with:
      * 'uptime_seconds': float of seconds since collector creation
      * 'total_submissions': int total of all task_name counts
      * 'status_distribution': copy of status_count
      * 'avg_latency': mean of latency_samples or None if empty
      * 'max_latency': max of latency_samples or None if empty
      * 'min_latency': min of latency_samples or None if empty
      * 'p95_latency': 95th percentile of latency_samples or None if empty
      * 'worker_stats': copy of worker_tasks
      * 'sample_count': length of latency_samples
    - `reset() -> None`: clear all counters and reset _start_time
    - `to_dict() -> dict`: serialize summary to dict (alias for get_summary)
    - `from_dict(data: dict) -> MetricsCollector`: class method to create a
      MetricsCollector from a dict. Restore _start_time from ISO string.

=== Component 7: Health Monitor ===

13. **HealthMonitor** class:

    Internal state:
    - `_checks`: dict mapping check_name to last result dict
    - `_lock`: threading.Lock
    - `_started_at`: datetime.datetime when the monitor was created

    Methods:
    - `__init__()`: initialize empty checks, set _started_at to now
    - `register_check(name: str, check_func: callable) -> None`: register a
      named health check function. The function takes no arguments and returns
      a dict with 'healthy' (bool) and optional 'details' (str).
    - `run_check(name: str) -> dict`: execute the named check, record the
      result with a timestamp. Return the result.
    - `run_all_checks() -> dict`: run all registered checks, return a dict
      mapping check name to result.
    - `get_status() -> dict`: return overall health status with:
      * 'overall_healthy': True if all checks are healthy
      * 'uptime_seconds': float of seconds since monitor creation
      * 'checks': copy of _checks dict
      * 'check_count': number of registered checks
    - `get_check_history(name: str, limit: int = 10) -> list[dict]`: return
      the last *limit* results for the named check.

=== Component 8: Configuration Manager ===

14. **QueueConfig** dataclass:

    Fields:
    - max_workers (int): maximum number of worker threads, default 4
    - worker_queue_size (int): max items each worker's internal queue holds,
      default 100
    - task_timeout_seconds (int): max time for a single task, default 300
    - retry_delay_seconds (int): delay between retries, default 5
    - http_host (str): HTTP server bind address, default '0.0.0.0'
    - http_port (int): HTTP server port, default 8080
    - max_retries (int): default max retries for tasks, default 3
    - queue_poll_interval (float): seconds to wait between queue polls,
      default 1.0
    - shutdown_timeout (float): seconds to wait for worker shutdown,
      default 10.0
    - metrics_enabled (bool): whether metrics collection is active,
      default True
    - persistence_enabled (bool): whether file persistence is active,
      default False
    - persistence_path (str | None): file path for persistence, default None
    - log_level (str): logging level string, default 'INFO'
    - log_format (str): logging format string, default '[%(name)s] %(levelname)s: %(message)s'
    - health_check_enabled (bool): whether health monitoring is active,
      default True
    - health_check_interval (int): seconds between health checks, default 60
    - max_task_age_hours (int): max age for tasks before cleanup, default 24
    - cleanup_interval_minutes (int): minutes between cleanup runs, default 60
    - metrics_flush_interval (int): seconds between metrics flushes, default 30
    - from_dict(data: dict) -> QueueConfig: class method to create config from dict
    - to_dict() -> dict: serialize config to dict

=== Demo Block ===

The `if __name__ == '__main__':` block must:
1. Create a TaskQueue instance
2. Register two handlers:
   a) `echo` handler -- returns {"echoed": payload, "processed_by": "echo-worker"}
      The handler should accept a dict and return it with an added "_processed_by" key
   b) `uppercase` handler -- uppercases all string values in the payload dict
      Uses a recursive approach to handle nested dicts and lists
3. Start 2 workers using start_workers(2)
4. Submit 5 tasks with varying priorities:
   - Task 1: name='echo', payload={'message': 'hello'}, priority=3
   - Task 2: name='uppercase', payload={'name': 'world'}, priority=7
   - Task 3: name='echo', payload={'message': 'world'}, priority=5
   - Task 4: name='uppercase', payload={'greeting': 'hi'}, priority=10
   - Task 5: name='echo', payload={'message': 'test'}, priority=1
5. Wait up to 10 seconds for all tasks to reach COMPLETED status:
   - Poll every 0.5 seconds
   - Check each task's status via get_task_status
6. Print final stats from get_stats()
7. Print results for each task ID via get_result
8. Shutdown workers via shutdown()

=== Quality Requirements ===

- ALL classes must have comprehensive docstrings explaining their purpose
- Type hints on ALL public methods, properties, constructor parameters,
  and function signatures
- Thread-safe operations using threading.Lock and threading.Condition
- Proper error handling throughout with descriptive error messages
- Use dataclasses for all data models (TaskRecord, QueueConfig)
- Use enum.IntEnum for TaskStatus with documented values
- Use the logging module for worker activity (configured per-worker)
- Code must be PEP 8 compliant with meaningful variable names
- Handle edge cases:
  - Empty queue dequeue (blocking with timeout)
  - Invalid JSON in HTTP requests (400 response)
  - Unknown task names in HandlerRegistry (KeyError)
  - Concurrent worker access to shared state (lock protection)
  - Duplicate task registration in HandlerRegistry (ValueError)
  - Invalid priority values outside 1-10 range (ValueError)
- The entire implementation must fit in this single module file
- Do NOT use any third-party libraries
- Use uuid.uuid4().hex for task IDs
- Use datetime.datetime.utcnow().isoformat() for timestamps
- Use json.dumps and json.loads for serialization
- Workers must use threading.Thread, NOT multiprocessing
- Use contextlib.closing or try/finally for worker cleanup
- Document the priority ordering convention in a module-level comment
- Include a module docstring describing the mini_queue package purpose
- Add module-level constants: MAX_WORKERS = 16, DEFAULT_QUEUE_SIZE = 1000,
  TASK_TIMEOUT_SECONDS = 300, RETRY_DELAY_SECONDS = 5
- Add module-level constants: HTTP_HOST = '0.0.0.0', HTTP_PORT = 8080
- Add module-level constants: WORKER_LOG_FORMAT = '[%(name)s] %(levelname)s: %(message)s'
- Add module-level constants: PRIORITY_MAX = 10, PRIORITY_MIN = 1
- Add module-level constants: MAX_RETRIES_DEFAULT = 3, QUEUE_POLL_INTERVAL = 1.0
- Add module-level constants: WORKER_SHUTDOWN_TIMEOUT = 10, TASK_LIST_SORT_KEY = 'created_at'
- Add module-level constant STATUS_LABELS dict mapping TaskStatus values to human-readable strings
- Document the cron field validation rules in a module-level docstring