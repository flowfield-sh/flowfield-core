"""Build and launch an owned, populated preview. Run: uv run python scripts/usage-preview.py."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[1]
    state = Path(tempfile.mkdtemp(prefix="flowfield-usage-preview-"))
    address = state / "address.json"
    child: subprocess.Popen | None = None
    requested_stop = False
    successful_exit = False
    env = {**os.environ, "FLOWFIELD_UPDATE_CHECKS": "0", "FLOWFIELD_DATA_DIR": str(state)}

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        print("Building Usage preview from this checkout…", flush=True)
        child = subprocess.Popen(
            ["pnpm", "--dir", "web", "build"],
            cwd=checkout,
            env=env,
            start_new_session=True,
        )
        if child.wait() != 0:
            raise RuntimeError("Frontend build failed; preview was not started.")
        with (state / "seed.json").open("w") as seeded_output:
            child = subprocess.Popen(
                [sys.executable, "tests/workspace_usage_browser_fixture.py", str(state)],
                cwd=checkout,
                env=env,
                stdout=seeded_output,
                start_new_session=True,
            )
            if child.wait() != 0:
                raise RuntimeError("Could not seed preview; see " + str(state))
        seeded = json.loads((state / "seed.json").read_text())
        with (state / "service.log").open("w") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "web/e2e/server.py",
                    str(state),
                    str(address),
                    str(os.getpid()),
                    str(args.port),
                ],
                cwd=checkout,
                start_new_session=True,
                stdout=log,
                stderr=log,
                env=env,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError((state / "service.log").read_text())
                if address.exists():
                    port = json.loads(address.read_text())["port"]
                    base = f"http://127.0.0.1:{port}"
                    try:
                        with urlopen(base + "/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                time.sleep(0.1)
            else:
                raise RuntimeError(
                    "Preview did not become ready; see " + str(state / "service.log")
                )
            print("Workspace Usage preview: " + base + seeded["workspace_url"], flush=True)
            print("Task Usage preview: " + base + seeded["url"], flush=True)
            print(
                "State/log: " + str(state) + "; queues paused; synthetic reports only.", flush=True
            )
            print(
                f"No companion service needed. Stop with Ctrl-C or kill -TERM {os.getpid()}; "
                "owned state is removed after confirmed shutdown.",
                flush=True,
            )
            if child.wait() != 0:
                raise RuntimeError("Preview service exited unexpectedly; see " + str(state))
            successful_exit = True
    except KeyboardInterrupt:
        requested_stop = True
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise SystemExit("Shutdown unconfirmed; retained state at " + str(state)) from None
        if requested_stop or successful_exit:
            shutil.rmtree(state)
            print("Preview stopped; owned state removed.", flush=True)
        else:
            print("Retained preview diagnostics: " + str(state), flush=True)


if __name__ == "__main__":
    main()
