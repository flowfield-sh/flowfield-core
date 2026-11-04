"""Local result command guardian. The service's stdin pipe is its lifetime lease.

Commands must stay in the owned process group (no daemonization). The guardian
cleans ordinary children even when the service crashes or the shell exits first.
No persisted PID is used to recover ownership. This is not sandbox containment.
"""

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    receipt, command = Path(sys.argv[1]), sys.argv[2]
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # The service pipe is a lifetime lease, never the program's input stream.
    child = subprocess.Popen(
        ["/bin/sh", "-c", command], start_new_session=True, stdin=subprocess.DEVNULL
    )
    clean = False
    try:
        while child.poll() is None and not stopping:
            readable, _, _ = select.select([sys.stdin], [], [], 0.1)
            if readable and not os.read(sys.stdin.fileno(), 1):
                stopping = True
        return child.returncode if child.returncode is not None else 130
    finally:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                clean = True
                break
            except PermissionError:
                pass  # Ownership is uncertain; keep checking within the stop bound.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                child.poll()
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    clean = True
                    break
                except PermissionError:
                    pass
                time.sleep(0.02)
            if clean:
                break
        receipt.write_text(json.dumps({"cleanup_confirmed": clean}))


if __name__ == "__main__":
    sys.exit(main())
