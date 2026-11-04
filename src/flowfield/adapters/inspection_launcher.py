"""Write a saved terminal launcher; preparation never executes project commands."""

import shlex
import sys
from pathlib import Path

from flowfield.adapters.local_execution import LocalAttempt


def write_launcher(environment: LocalAttempt, setup: list[str], run: str) -> Path:
    launcher = environment.root / "try-result.sh"
    # Save only managed paths. The terminal supplies host credentials and native settings.
    variables = environment.launch_environment()
    variables = {
        key: variables[key]
        for key in (
            "PWD",
            "TMPDIR",
            "TMP",
            "TEMP",
            "FLOWFIELD_RUN_ID",
            "FLOWFIELD_WORKSPACE",
            "FLOWFIELD_RUNTIME_DIR",
            "FLOWFIELD_OUTPUT_DIR",
            "FLOWFIELD_CACHE_DIR",
        )
        if key in variables
    }
    exports = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in variables.items())
    # Outside the worktree: preview edits and generated launcher files cannot enter delivery.
    # Exclusive creation prevents accidental replacement of an existing copy's instructions.
    with launcher.open("x") as stream:
        stream.write(
            "#!/bin/sh\n"
            "# Saved Flowfield inspection. Run explicitly; stop with Ctrl+C.\n"
            "set -e\n"
            f"cd {shlex.quote(str(environment.checkout))}\n"
            + (
                "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR "
                "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE\n"
            )
            + exports
            + "temporary=$(mktemp -d /tmp/ff-r-terminal-XXXXXX)\n"
            + 'trap \'rm -f "$temporary/t"; rmdir "$temporary"\' EXIT\n'
            + 'ln -s "$TMPDIR" "$temporary/t"\n'
            + 'export TMPDIR="$temporary/t" TMP="$temporary/t" TEMP="$temporary/t"\n'
            + "export FLOWFIELD_PORT=$("
            + shlex.quote(sys.executable)
            + " -c "
            + shlex.quote(
                'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); '
                "print(s.getsockname()[1]); s.close()"
            )
            + ")\n"
            + "\n".join([*setup, run]).rstrip()
            + "\n"
        )
    launcher.chmod(0o600)
    return launcher
