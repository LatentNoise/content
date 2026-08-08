"""The single external-process runner.

Adapted from HomeTube's ``engine.runner.run_cmd`` with the fixes identified in
the audit: mandatory timeout, separated stdout/stderr log files, cooperative
cancellation that also works when the process emits no output (watchdog
thread), and no shell — argument lists only.
"""

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_KILL_GRACE_SECONDS = 5


@dataclass
class ProcessResult:
    returncode: int
    cancelled: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and not self.timed_out


def run_process(
    args: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    stdout_log: Path,
    stderr_log: Path,
    cancel_check: Callable[[], bool] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ProcessResult:
    """Run *args*, teeing stdout to *stdout_log* (and *on_line*) and stderr to
    *stderr_log*. Returns instead of raising for timeout/cancellation so the
    caller maps outcomes to normalized step errors."""
    cancel_check = cancel_check or (lambda: False)
    deadline = time.monotonic() + timeout_seconds
    state = {"cancelled": False, "timed_out": False}

    with stdout_log.open("w") as out_file, stderr_log.open("w") as err_file:
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=err_file,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            # Missing/unusable binary is a normalized failure, never a crash.
            err_file.write(f"failed to start {args[0]!r}: {exc}\n")
            return ProcessResult(returncode=127)

        def _terminate() -> None:
            proc.terminate()
            try:
                proc.wait(timeout=_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()

        def _watchdog() -> None:
            while proc.poll() is None:
                if cancel_check():
                    state["cancelled"] = True
                    _terminate()
                    return
                if time.monotonic() > deadline:
                    state["timed_out"] = True
                    _terminate()
                    return
                time.sleep(0.3)

        watchdog = threading.Thread(target=_watchdog, daemon=True)
        watchdog.start()
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                out_file.write(line + "\n")
                if on_line is not None:
                    on_line(line)
        finally:
            returncode = proc.wait()
            watchdog.join(timeout=_KILL_GRACE_SECONDS + 1)

    return ProcessResult(
        returncode=returncode,
        cancelled=state["cancelled"],
        timed_out=state["timed_out"],
    )
