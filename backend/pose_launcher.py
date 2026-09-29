"""Keep a pose runner and its descendants tied to their HTTP server's lifetime.

This tiny supervisor runs before optional Torch imports. On Linux, parent death
raises SIGTERM even after an abrupt server crash. The runner has its own isolated
process group; its helpers are cleaned up on cancellation and ordinary exit.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        raise ValueError("pose_launcher requires parent PID -- runner argv")
    expected_parent = int(sys.argv[1])
    child: subprocess.Popen | None = None
    pending_signal: int | None = None
    if os.getpgrp() != os.getpid():
        raise ValueError("pose_launcher requires an isolated process group")

    def stop(signum: int, _frame: object) -> None:
        nonlocal pending_signal
        if child is None:
            # A signal can arrive inside Popen before it returns its PID. Finish
            # that spawn, then terminate the now-known group below.
            pending_signal = signum
            return
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal_runner(signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                break
            time.sleep(.02)
        # WNOWAIT leaves the group leader's PID reserved until all its helpers
        # have been killed, even if the runner itself already exited.
        signal_runner(signal.SIGKILL)
        os.killpg(os.getpgrp(), signal.SIGKILL)
        raise SystemExit(128 + signum)

    def signal_runner(signum: int) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "could not attach pose worker to server lifetime")
    # Cover the race where the server died before prctl was installed.
    if os.getppid() != expected_parent or pending_signal is not None:
        return 143
    child = subprocess.Popen(sys.argv[3:], start_new_session=True)
    if os.getppid() != expected_parent or pending_signal is not None:
        stop(pending_signal or signal.SIGTERM, None)
    os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
    # A custom runner may finish while leaving background helpers alive.
    # The unreaped leader reserves this group ID throughout cleanup.
    signal_runner(signal.SIGTERM)
    time.sleep(.03)
    signal_runner(signal.SIGKILL)
    code = child.wait()
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
