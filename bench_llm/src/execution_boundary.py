"""Execution boundary for benchmark-generated code (security hardening, P0).

Security invariant
------------------
*The benchmark tests the model; the model does not inherit trust from the
benchmark host.*

Model-generated Python or Java is **never** executed in-process and is **never**
executed with the full host environment.  Generated code runs as a disposable
child process behind a clearly defined execution boundary that:

* inherits **no inherited secrets / credentials / HOME** -- only a minimal,
  explicit allow-list of benign variables (never ``os.environ`` wholesale);
* runs from an **isolated, disposable working directory** containing only the
  files required by the individual case -- no repository root, user home, SSH
  directories, git credentials, API/config dirs, or arbitrary host mounts;
* is subject to a **hard wall-clock timeout**;
* does **not** open a console window on Windows (``CREATE_NO_WINDOW``);
* **fails closed**: if the secure runner cannot be started
  (:class:`ExecutionBoundaryError`) it is reported as an execution /
  infrastructure failure. There is *no* silent fall back to unrestricted host
  execution.

Platform boundary (stated honestly)
-----------------------------------
This default boundary runs generated code as a **host subprocess** with the
controls above.  On a typical Windows development host there is no lightweight,
dependency-free OS-level network-egress block or absolute-file-system sandbox for
an arbitrary child process -- that requires a container/namespace (e.g. Docker /
LCOW) or a Windows Job Object with net-io limits, which are *not* assumed to be
available and which would make the offline test suite fail on machines without
them.  The interface here is intentionally decoupled so a more restrictive runner
can be swapped in later.  Until then, host resource access beyond environment /
credentials / workspace-relative paths is documented as an **uncontrolled**
boundary -- it is *not* claimed to be a sandbox.

The controls that ARE genuinely enforced by this boundary and covered by tests:
inherited secrets/credentials are not visible to generated code, and generated
code runs confined to an isolated temporary workspace (it cannot traverse up into
the benchmark repository by relative path).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Union

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default hard timeout (seconds) applied to every generated-code execution.
DEFAULT_TIMEOUT_SECONDS = 60.0

#: Benign, non-secret environment variables that may be forwarded *only if present*
#: in the host process.  This is an explicit allow-list -- never ``os.environ``.
#: It deliberately excludes HOME / HOMEPATH / HOSTNAME and anything credential-like.
BENIGN_ENV_ALLOWLIST: tuple[str, ...] = (
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "NUMBER_OF_PROCESSORS",
)

PathLike = Union[str, os.PathLike[str]]


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecOutcome:
    """Result of running generated code under the execution boundary.

    Mirrors the fields validators already consumed from ``subprocess.CompletedProcess``
    plus an explicit ``timed_out`` flag (``TimeoutExpired`` no longer escapes).
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class ExecutionBoundaryError(RuntimeError):
    """Raised when the secure runner cannot be started.

    Callers must treat this as a **fail-closed** execution / infrastructure
    failure and MUST NOT fall back to unrestricted host execution of generated
    code.
    """


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

def build_isolated_env(
    executable_dirs: Optional[Sequence[str]] = None,
    allowlist: Iterable[str] = BENIGN_ENV_ALLOWLIST,
) -> dict[str, str]:
    """Build a minimal, secret-stripped environment.

    Only the directories of the executables that will be launched are placed on
    ``PATH`` so the child can locate its interpreter/compiler.  No other host
    variables -- and in particular no secrets, credentials, ``HOME``, or network
    configuration -- are inherited.

    Args:
        executable_dirs: Directories containing the binaries to run (e.g. the
            directory of ``python`` / ``javac``).  Used solely to build a scoped
            ``PATH``; never used to locate secrets.
        allowlist: Benign variable names optionally forwarded when present in the
            host process.  Defaults to :data:`BENIGN_ENV_ALLOWLIST`.

    Returns:
        A fresh environment dict with no inherited secrets.
    """
    path_dirs: list[str] = []
    for d in executable_dirs or []:
        if d and d not in path_dirs:
            path_dirs.append(d)

    env: dict[str, str] = {}
    if path_dirs:
        env["PATH"] = os.pathsep.join(path_dirs)

    for name in allowlist:
        value = os.environ.get(name)
        if value:
            env[name] = value

    return env


def _resolve_executable(program: str) -> str:
    """Resolve *program* to an absolute path; fail closed if it cannot be found.

    Resolving to an absolute path removes ``PATH`` ambiguity so the child runs the
    exact interpreter/compiler we intend, regardless of the scoped PATH we hand it.
    """
    resolved = shutil.which(program)
    if not resolved:
        raise ExecutionBoundaryError(f"secure runner executable not found: {program!r}")
    return os.path.abspath(resolved)


def _creation_flags() -> int:
    """Windows flag that prevents a console window from flashing for child processes."""
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0


# ---------------------------------------------------------------------------
# Core execution entry point
# ---------------------------------------------------------------------------

def run_generated_code(
    args: Sequence[str],
    *,
    cwd: PathLike,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    executable_dirs: Optional[Sequence[str]] = None,
) -> ExecOutcome:
    """Run model-generated code under the execution boundary.

    Args:
        args: The command to run; ``args[0]`` is the program (e.g. ``"python"``,
            ``"javac"``, ``"java"``).  No shell is used (no ``shell=True``), so no
            command injection / globbing occurs.
        cwd: Isolated working directory for this case.  Only the files required by
            the case should live here; nothing else on the host is exposed.
        timeout: Hard wall-clock limit in seconds.
        executable_dirs: Directories to place on the child's scoped ``PATH``.

    Returns:
        :class:`ExecOutcome` with the captured exit code and stdout/stderr.

    Raises:
        ExecutionBoundaryError: if the runner executable cannot be located or the
            child fails to start. Callers must map this to a fail-closed failure;
            they must NOT fall back to running the generated code on the host.
        subprocess.TimeoutExpired: propagated verbatim so callers can preserve their
            existing timeout handling (timed-out runs yield score 0).
    """
    if not args:
        raise ExecutionBoundaryError("no command supplied to execution boundary")

    program = args[0]
    if not isinstance(program, str) or not program.strip():
        raise ExecutionBoundaryError(f"invalid executable in execution boundary: {program!r}")

    exe_path = _resolve_executable(program)
    env = build_isolated_env(executable_dirs or [os.path.dirname(exe_path)])

    try:
        proc = subprocess.run(
            [exe_path, *list(args[1:])],
            cwd=os.fspath(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired:
        # Preserve existing validator timeout handling.
        raise
    except (FileNotFoundError, OSError, ValueError) as exc:
        # The secure runner could not start -- fail closed, never fall back.
        raise ExecutionBoundaryError(
            f"secure runner failed to start for {program!r}: {exc!r}"
        ) from exc

    return ExecOutcome(
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        timed_out=False,
    )


# ---------------------------------------------------------------------------
# Object-oriented boundary (swap-in point for a more restrictive runner)
# ---------------------------------------------------------------------------

class ExecutionBoundary:
    """Encapsulates env isolation + execution so validator code never touches
    ``subprocess`` directly for generated-code execution.

    A stricter implementation (container / namespace based) can be dropped in by
    subclassing or replacing :meth:`run` without touching the validators, which
    depend only on this interface plus :func:`run_generated_code`.
    """

    def __init__(
        self,
        executable_dirs: Optional[Sequence[str]] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        allowlist: Iterable[str] = BENIGN_ENV_ALLOWLIST,
    ) -> None:
        self.executable_dirs = list(executable_dirs or [])
        self.timeout = timeout
        self.allowlist = tuple(allowlist)

    def environment(self) -> dict[str, str]:
        """Return the minimal, secret-stripped environment this boundary uses."""
        return build_isolated_env(self.executable_dirs, self.allowlist)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: PathLike,
        timeout: Optional[float] = None,
    ) -> ExecOutcome:
        """Run generated code under this boundary."""
        return run_generated_code(
            args,
            cwd=cwd,
            timeout=DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout,
            executable_dirs=self.executable_dirs,
        )


#: Shared default boundary used by the validators.  Generated-code execution in
#: the benchmark always flows through here -- never a bare ``subprocess.run``.
default_boundary = ExecutionBoundary()
