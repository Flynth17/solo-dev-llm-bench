"""Canonical evaluation speed prompts.

Backend-owned fixed prompts for speed-benchmarking three different workload sizes.
These are deterministic, static, and realistic solo-developer engineering tasks.

Target approximate INPUT sizes:
    small  ~250 tokens
    medium ~1,000 tokens
    large  ~4,000 tokens
"""

from __future__ import annotations

SPEED_PROMPTS: dict[str, str] = {
    "small": (
        "Write a Python function called `normalize_emails` that takes a list of email "
        "strings and returns a new list with every email lowercased, leading/trailing "
        "whitespace stripped, and any duplicate entries removed while preserving the "
        "original first occurrence order. Include a docstring and type hints. The "
        "function should skip empty strings silently and raise a ValueError if any "
        "entry is not a string type."
    ),
    "medium": (
        "You are building a lightweight CLI tool for a solo developer who manages "
        "multiple Python microservices. Write a Python module called `project_lifecycle` "
        "that provides the following capabilities:\n\n"
        "1. `ServiceConfig` dataclass with fields: name (str), port (int), "
        "health_check_path (str), dependencies (list[str]), and is_enabled (bool, "
        "defaults True).\n\n"
        "2. A function `load_services(config_path: str) -> list[ServiceConfig]` that "
        "reads a YAML-like plain-text config file (one service per block separated by "
        "double newlines, with `key: value` lines inside each block) and returns a "
        "list of ServiceConfig objects. Skip blank lines and lines starting with `#`. "
        "If a required field is missing, skip that service and log a warning string "
        "to stdout.\n\n"
        "3. A function `build_startup_order(services: list[ServiceConfig]) -> list[str]` "
        "that returns the names of services in topological order based on their "
        "dependencies. If a circular dependency is detected, raise a "
        "``ValueError('Circular dependency detected')``.\n\n"
        "4. A function `generate_readme(services: list[ServiceConfig]) -> str` that "
        "produces a Markdown-formatted README snippet listing each enabled service, "
        "its port, and its health-check URL (formatted as "
        "`http://localhost:<port><health_check_path>`). Disabled services should be "
        "commented out with `[DISABLED]` prefix.\n\n"
        "Include a `if __name__ == '__main__':` block that demonstrates loading a "
        "sample config, building the startup order, and printing the generated README "
        "snippet. Use only the Python standard library."
    ),
    "large": (
        "You are designing a minimal but complete REST API framework from scratch for "
        "a solo developer who needs something lightweight without the overhead of "
        "Django or FastAPI. Implement everything in a single Python module called "
        "`micro_rest` using only the standard library (http.server, json, urllib.parse, "
        "uuid, datetime, threading).\n\n"
        "=== Core Components ===\n\n"
        "1. `Route` dataclass:\n"
        "   - path (str): the URL pattern, e.g. `/users/<user_id>`\n"
        "   - methods (list[str]): allowed HTTP methods, e.g. `[\"GET\", \"POST\"]`\n"
        "   - handler (callable): the function to invoke\n"
        "   - middleware (list[callable]): optional list of middleware functions\n\n"
        "2. `Router` class:\n"
        "   - `add_route(path: str, methods: list[str], handler: callable, "
        "middleware: list[callable] | None = None) -> None`\n"
        "   - `match(method: str, path: str) -> tuple[Route, dict[str, str]] | None` "
        "that matches an incoming request against registered routes and returns the "
        "matched route plus a dict of extracted path parameters (e.g. "
        "`{\"user_id\": \"42\"}` from `/users/42`). Support `<name>` style path "
        "parameters.\n\n"
        "3. `Middleware` base class with:\n"
        "   - `before_request(request) -> dict[str, str] | None` — if returns None, "
        "continue; if returns dict, merge into request context\n"
        "   - `after_request(response) -> dict | None` — if returns None, keep "
        "response as-is\n\n"
        "4. Built-in middleware classes:\n"
        "   - `TimingMiddleware`: measures request processing time, adds "
        "   `X-Response-Time` header\n"
        "   - `LoggingMiddleware`: logs method, path, and timestamp to stdout\n\n"
        "5. `Application` class:\n"
        "   - `__init__()` — initializes internal router and middleware registry\n"
        "   - `use(middleware: type[Middleware]) -> None` — registers a middleware "
        "globally\n"
        "   - `get(path: str, handler: callable) -> None` — shortcut for GET route\n"
        "   - `post(path: str, handler: callable) -> None` — shortcut for POST route\n"
        "   - `run(host: str = \"127.0.0.1\", port: int = 8000) -> None` — starts "
        "the built-in HTTP server, dispatches requests through the router and "
        "middleware pipeline\n\n"
        "6. Request/Response handling:\n"
        "   - Requests should support path parameters, query parameters, and JSON "
        "request bodies\n"
        "   - Handlers receive `(request) -> dict` and return `(status_code, response_dict)`\n"
        "   - Responses are automatically JSON-serialized with appropriate Content-Type "
        "headers\n\n"
        "7. `if __name__ == '__main__':` demo block:\n"
        "   - Create an Application with in-memory storage\n"
        "   - Register CRUD routes for a `/items` resource (GET all, GET by ID, POST, "
        "PUT, DELETE)\n"
        "   - Register a `/health` route that returns `{\"status\": \"ok\"}`\n"
        "   - Start the server on port 8000\n\n"
        "=== Quality Requirements ===\n\n"
        "- All classes must have docstrings\n"
        "- Type hints on all public methods\n"
        "- Proper error handling: 404 for unmatched routes, 405 for disallowed "
        "methods, 400 for malformed JSON\n"
        "- Thread-safe in-memory storage (use threading.Lock)\n"
        "- The code must be PEP 8 compliant and well-organized\n"
        "- Handle edge cases: empty request bodies, invalid JSON, missing headers\n\n"
        "=== Implementation Notes ===\n\n"
        "- Use `http.server.HTTPServer` and `http.server.BaseHTTPRequestHandler` as "
        "the foundation\n"
        "- Parse query parameters with `urllib.parse`\n"
        "- Read JSON request body from `request.rfile.read(int(request.headers[\"Content-Length\"]))`\n"
        "- Generate unique IDs with `uuid.uuid4()`\n"
        "- Store timestamps with `datetime.datetime.utcnow().isoformat()`\n"
        "- Do NOT use any third-party libraries\n"
        "- The entire implementation must fit in this single module file"
    ),
}


def get_speed_prompt(name: str) -> str:
    """Return the canonical speed prompt for the given size label.

    Args:
        name: One of ``"small"``, ``"medium"``, or ``"large"``.

    Returns:
        The prompt string.

    Raises:
        ValueError: If *name* is not a recognized prompt label.
    """
    if name not in SPEED_PROMPTS:
        raise ValueError(
            f"Unknown evaluation speed prompt: {name!r}. "
            f"Expected one of {sorted(SPEED_PROMPTS)}."
        )
    return SPEED_PROMPTS[name]