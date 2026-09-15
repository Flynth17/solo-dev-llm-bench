# Security Model — Solo Dev LLM Bench

**Invariant:** *The benchmark tests the model; the model does not inherit trust from
the benchmark host.*

Model-generated Python and Java are executed behind an **execution boundary** so that a
model that has not yet earned trust can never run with unrestricted access to the normal
host environment (filesystem, home/credentials, secrets, network, other processes).

This document states what is enforced, what is deliberately out of scope on this platform,
and how it is tested. It does not alter benchmark scoring, corpora, or result compatibility.

---

## P0 — Generated-code execution boundary

**Module:** `bench_llm/src/execution_boundary.py`
**Used by:** `src.python_validator`, `src.java_validator`, `src.quality.python_cases`,
`src.quality.java_cases` (all generated-code execution now flows through it; validators no
longer call `subprocess.run` directly for model output).

Every run of model-generated code goes through `run_generated_code(...)` / `ExecutionBoundary`:

| Control | Enforced? | Detail |
|---|---|---|
| No in-process execution | ✅ | Model code runs as a child process, never `eval`/`exec`/`compile()` in BenchLLM. |
| Inherited secrets stripped | ✅ | Env is built from an explicit allow-list (`PATH`, `SYSTEMROOT`, `TEMP`, `TMP`, `NUMBER_OF_PROCESSORS`) — **never** `os.environ`. Secrets / credentials are not forwarded. |
| No host HOME / credentials exposed | ✅ | `HOME`/`HOMEPATH` are not forwarded; `~` degrades to `'~'`, so credential paths cannot resolve. |
| Isolated workspace | ✅ | Child runs in a disposable temp dir containing only that case's files, rooted there as its cwd. |
| No repo / home / config mounts | ✅ | Nothing on the host is mounted; only the case files are written into the workspace. |
| Hard timeout | ✅ | Wall-clock `timeout` (default 60s) enforced per execution. |
| Fail closed | ✅ | If the runner cannot start (`ExecutionBoundaryError`) the case reports an execution/infrastructure failure — score 0, `passed=False`. There is **no** silent fallback to unrestricted host execution. |
| No console window (Windows) | ✅ | `CREATE_NO_WINDOW` set on spawned processes. |

### Documented platform limitation (not a sandbox claim)
On a typical **Windows development host** there is no dependency-free, OS-level block on
network egress or absolute file-system access for an arbitrary child process without a
container/namespace (Docker/LCOW) or a Windows Job Object with net-io limits. Those are
**not assumed available** here and would break the offline test suite on machines lacking
them. Therefore:

- The boundary enforces env/credentials/workspace isolation, which genuinely prevents the
  resources above from being *inherited* or reached by relative traversal.
- **Raw socket egress and absolute cross-directory file access are NOT OS-blocked** by this
  default host-process boundary on this platform. This is documented rather than claimed as
  a sandbox. A more restrictive runner (container/namespace) can be dropped in later behind
  the same `ExecutionBoundary` interface without touching the validators.

The required regression tests prove the controls that ARE enforced (env secret not visible,
credential path blocked, workspace confinement, fail-closed, timeout). See
`tests/test_execution_boundary_isolation.py`, `tests/test_java_execution_isolation.py`.

---

## P1 — Backend URL / SSRF boundary

**Module:** `bench_llm/src/backend_url_policy.py`
**Wired into:** `POST /api/config` (persisted URLs), GET `/api/models`,
`POST /api/models/load|unload`, and the Workflow/Speed launch routes (`lm_studio_url`
overrides) — all reject a disallowed host with **HTTP 400** before any request is made.

Policy:

- Default allow-list is **loopback only**: `127.0.0.1` and `localhost`.
- Scheme restricted to `http` / `https`; malformed URLs rejected.
- Arbitrary public or private destinations are **rejected by default**.
- Trusted addresses can be added opt-in via `trusted_backend_hosts` in
  `config/settings.json` (host/IP literals; scheme+port ignored for matching). A missing or
  unreadable config falls back to the secure loopback-only default.

The API therefore never becomes a generic HTTP requester toward arbitrary destinations. See
`tests/test_backend_url_policy.py`.

---

## P2 — LAN exposure review

**Finding:** `server_launcher.py` binds uvicorn to **`127.0.0.1:8000`** (loopback) only.
There is no `--host` flag and no `0.0.0.0` binding anywhere in `src/`, so the API is not
reachable from the LAN by default. No authentication framework was added — the secure
default (loopback binding + loopback-only backend routing) removes the external attack
surface targeted here. See `tests/test_lan_boundary.py`.

> If `--host 0.0.0.0` were ever introduced, it would change this threat model by exposing
> every endpoint (benchmark execution, result mutation, config/backend changes) to the LAN.
> Any such change must be explicit and documented; it is not present today.

---

## Regression evidence

The full existing suite continues to pass with unchanged scoring/result compatibility:
environment stripping does not affect benchmark behavior because no validator or case
depends on inherited environment variables (verified by the regression run).
