"""Explicit model-free native catalog service-loss check with retained isolated state."""

import argparse
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from flowfield.adapters import claude_install
from flowfield.adapters.claude_agent import model_options
from flowfield.adapters.local_process import LocalProcess
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessEdit
from flowfield.harness_settings import HarnessSettings
from flowfield.supervisor import Supervisor


async def child(trial: Path, bundle: Path, native: Path) -> None:
    workspace = Workspace(trial / "state")
    claude_install.install(workspace.directory, bundle, claude_install.digest(bundle))
    config = trial / "native-config"
    config.mkdir()
    helper = trial / "hook.py"
    helper.write_text(
        "import os,time\nfrom pathlib import Path\n"
        f"Path({str(trial / 'hook.pid')!r}).write_text(str(os.getpid()))\n"
        "time.sleep(25)\n"
    )
    (config / "settings.json").write_text(
        json.dumps(
            {
                "model": "sonnet",
                "availableModels": ["sonnet"],
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": " ".join(
                                        shlex.quote(value)
                                        for value in [sys.executable, str(helper)]
                                    ),
                                }
                            ]
                        }
                    ]
                },
            }
        )
    )
    registration = HarnessSettings(workspace).edit(
        "claude-code",
        HarnessEdit(
            expected_revision=1,
            executable=str(native),
            config_directory=str(config),
        ),
    )
    service = Supervisor(workspace)
    await service.start()
    try:
        with patch("flowfield.catalogs.model_options", model_options):
            await service.catalogs.run(registration)
    finally:
        await service.close()


def live(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # Observation only, never saved-PID termination authority.
        return True
    except ProcessLookupError:
        return False


async def probe(trial: Path, bundle: Path, native: Path) -> dict:
    home = trial / "home"
    home.mkdir()
    owner = await LocalProcess.start(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            str(bundle),
            "--native",
            str(native),
            "--child",
            str(trial),
        ],
        cwd=trial,
        env={
            "HOME": str(home),
            "PATH": "",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "FLOWFIELD_UPDATE_CHECKS": "0",
        },
    )

    async def drain(stream):
        while await stream.read(65536):
            pass  # No raw native diagnostics/account data retained.

    drains = [
        asyncio.create_task(drain(stream))
        for stream in [owner.process.stdout, owner.process.stderr]
    ]
    try:
        async with asyncio.timeout(35):
            while not (trial / "hook.pid").exists():
                if owner.process.returncode is not None:
                    raise RuntimeError("Fixture discovery exited before native hook startup")
                await asyncio.sleep(0.1)
        pid = int((trial / "hook.pid").read_text())
        assert live(pid)
        workspace = Workspace(trial / "state")
        before = Supervisor(workspace).catalogs.ownership("claude-code")
        assert before is not None and before.status == "running"
        # Only the currently owned fixture-service group is stopped.
        assert await owner.close(timeout=2)
        restored = Supervisor(workspace)
        await restored.start()
        try:
            hold = restored.catalogs.ownership("claude-code")
            assert hold is not None and hold.id == before.id and hold.status == "uncertain"

            async def forbidden(*args, **kwargs):
                raise AssertionError("Native discovery replayed")

            with patch("flowfield.catalogs.model_options", forbidden):
                try:
                    await restored.catalogs.run(
                        HarnessSettings(workspace).get("claude-code"), refresh=True
                    )
                except ApplicationError as error:
                    assert error.code == "catalog_cleanup_unconfirmed"
                else:
                    raise AssertionError("Unknown cleanup released discovery capacity")
            async with asyncio.timeout(35):
                while live(pid):
                    await asyncio.sleep(0.1)
            assert restored.catalogs.ownership("claude-code").status == "uncertain"
            return {
                "passed": True,
                "noPromptSent": True,
                "nativeHookObservedLive": True,
                "restoredUncertain": True,
                "noReplay": True,
                "helperEventuallyGone": True,
                "helperAbsenceIsNotCleanup": True,
                "personalServicesStarted": False,
            }
        finally:
            await restored.close()
    finally:
        await owner.close(timeout=2)
        await asyncio.gather(*drains)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--trial-root", type=Path)
    parser.add_argument("--invoke-native", action="store_true")
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    bundle, native = args.bundle.resolve(strict=True), args.native.resolve(strict=True)
    if args.child:
        asyncio.run(child(args.child, bundle, native))
        return
    if not args.invoke_native or not args.trial_root:
        parser.error("Explicit --invoke-native and dedicated --trial-root are required")
    root = args.trial_root.resolve(strict=True)
    source = Path(__file__).resolve().parents[1]
    if source.parent in (root, *root.parents) or any(
        path in (root, *root.parents)
        for path in [
            Path("/tmp").resolve(),
            Path("/var/folders").resolve(),
        ]
    ):
        parser.error("Use a dedicated trial root outside source/shared temporary state")
    trial = root / ("h1-catalog-restart-" + uuid4().hex)
    trial.mkdir(mode=0o700)
    report = asyncio.run(probe(trial, bundle, native))
    (trial / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"trial": str(trial), **report}, indent=2))


if __name__ == "__main__":
    main()
