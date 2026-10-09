"""Explicit, signed-out Claude capability proof; never sends a model prompt.

Build bridges/claude-acp first. This copies the standalone artifact outside its
build tree, supplies an explicit native executable, and empties PATH and native
configuration. It uses the shared ACP owner, not a second adapter lifecycle.
"""

import argparse
import asyncio
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from acp.exceptions import RequestError
from acp.schema import HttpMcpServer

from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.claude_cleanup import (
    CLAUDE_SHUTDOWN_TIMEOUTS,
    quiesce,
    require_cleanup,
)
from flowfield.adapters.local_process import LocalProcess


async def command(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    owner = await LocalProcess.start(args, cwd=cwd, env=env)
    process = owner.process
    assert process.stdout and process.stderr

    async def discard(stream: asyncio.StreamReader) -> None:
        while await stream.read(8192):
            pass

    diagnostics = asyncio.create_task(discard(process.stderr))
    output = bytearray()
    try:
        async with asyncio.timeout(45):
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 262144:
                    raise RuntimeError("Proof output exceeds its bound")
            await process.wait()
    finally:
        group_exited = await owner.close()
        diagnostics.cancel()
        await asyncio.gather(diagnostics, return_exceptions=True)
    assert group_exited
    if process.returncode:
        raise RuntimeError(f"Proof command failed: exit {process.returncode}")
    return output.decode().strip()


async def probe(bridge: Path, native: Path, scratch: Path, *, cleanup: bool = False) -> dict:
    spec = json.loads(
        (Path(__file__).resolve().parents[1] / "bridges/claude-acp/proof.json").read_text()
    )
    executable = scratch / "claude-acp-proof"
    shutil.copy2(bridge, executable)
    home = scratch / "home"
    home.mkdir()
    project = scratch / "project"
    project.mkdir()
    unexpected_logs = scratch / "unexpected-logs"
    (project / ".env").write_text(f"CLAUDE_AGENT_LOGS={unexpected_logs}\n")
    (project / "bunfig.toml").write_text('preload = ["./must-not-load.js"]\n')
    (project / "must-not-load.js").write_text('throw new Error("Unexpected preload");\n')
    env = {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_CODE_EXECUTABLE": str(native),
        "PATH": "",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    report = {
        "noPromptSent": True,
        "outsideBuildTree": True,
        "emptyPath": True,
        "isolatedNativeConfig": True,
    }
    report["bridgeVersion"] = await command([str(executable), "--version"], project, env)
    assert report["bridgeVersion"] == spec["proof_version"]
    report["nativeVersion"] = await command([str(executable), "--cli", "--version"], project, env)
    assert report["nativeVersion"] == f"{spec['measured_native_version']} (Claude Code)"
    report["nativeControl"] = json.loads(
        await command([str(executable), "--flowfield-proof-native-control"], project, env)
    )
    assert report["nativeControl"]["nativeOwnerExitObserved"]
    assert not report["nativeControl"]["forcedExit"]
    assert report["nativeControl"]["backgroundSnapshotSizes"] == [0]
    assert any(
        model.get("resolvedModel") == spec["test_model"]
        for model in report["nativeControl"]["models"]
    )
    client = AcpSession(lambda _: None, request_timeout=30)
    try:
        await client.start(
            [str(executable)],
            cwd=project,
            env=env,
            mcp_servers=[
                HttpMcpServer(
                    name="flowfield_probe", type="http", url="http://127.0.0.1:1/probe", headers=[]
                )
            ],
        )
        report["capabilities"] = client.capabilities
        report["config"] = client.config
        assert {"mode", "model", "effort", "fast"} <= {item["id"] for item in client.config}
        initial_session = client.session_id
    finally:
        receipt = await client.close()
    report["stop"] = asdict(receipt)
    assert receipt.process_group_exited
    # Released session close is not a native-work cleanup receipt.
    assert receipt.owned_work_stopped is None
    assert "flowfield.cleanup" not in client.capabilities.get("_meta", {})

    alternate_config = scratch / "alternate-native-config"
    alternate_config.mkdir()
    (alternate_config / "settings.json").write_text(
        json.dumps({"model": "sonnet", "availableModels": ["sonnet"]})
    )
    configured = AcpSession(lambda _: None, request_timeout=30)
    try:
        await configured.start(
            [str(executable)],
            cwd=project,
            env={**env, "CLAUDE_CONFIG_DIR": str(alternate_config)},
            mcp_servers=[],
        )
        models = next(item for item in configured.config if item["id"] == "model")
        assert models["currentValue"] == "sonnet"
        available = [item["value"] for item in models["options"]]
        # Native availableModels retains the Default option by design.
        assert available == ["default", "sonnet"], available
        report["nativeConfigDirectoryOverride"] = {
            "selectedModel": models["currentValue"],
            "availableModels": available,
        }
    finally:
        configured_receipt = await configured.close()
    assert configured_receipt.process_group_exited

    # An initialized session with no turn does not establish persisted history.
    resumed = AcpSession(lambda _: None, request_timeout=30)
    try:
        await resumed.start(
            [str(executable)],
            cwd=project,
            env=env,
            mcp_servers=[],
            resume_session_id=initial_session,
        )
    except RequestError as error:
        assert error.code == -32002, error.code
        report["emptySessionResume"] = "rejected"
    else:
        report["emptySessionResume"] = "accepted"
    finally:
        resumed_receipt = await resumed.close()
    assert resumed_receipt.process_group_exited
    assert report["emptySessionResume"] == "rejected"

    guarded = AcpSession(lambda _: None, request_timeout=30)
    try:
        await guarded.start(
            [str(executable), "--hide-claude-auth"], cwd=project, env=env, mcp_servers=[]
        )
    except RequestError as error:
        assert error.code == -32000, error.code
        report["signedOutAuthGuard"] = "authentication_required"
    else:
        raise AssertionError("The guarded signed-out session was accepted")
    finally:
        guarded_receipt = await guarded.close()
    assert guarded_receipt.process_group_exited

    failed = AcpSession(lambda _: None, request_timeout=30)
    try:
        await failed.start([str(executable)], cwd=project, env=env, mcp_servers=[])
        assert failed.process
        failed.process.kill()  # Only this proof's owned bridge, with no prompt dispatched.
        await asyncio.wait_for(failed.process.wait(), 10)
    finally:
        failed_receipt = await failed.close()
    report["idleBridgeFailureStop"] = asdict(failed_receipt)
    assert failed_receipt.process_group_exited
    assert failed_receipt.owned_work_stopped is None
    assert not unexpected_logs.exists()
    report["projectAutoloadDisabled"] = True
    if cleanup:
        candidate = AcpSession(
            lambda _: None,
            request_timeout=30,
            cleanup=quiesce,
            shutdown_timeouts=CLAUDE_SHUTDOWN_TIMEOUTS,
        )
        try:
            await candidate.start(
                [str(executable), "--flowfield-proof-cleanup"],
                cwd=project,
                env=env,
                mcp_servers=[],
            )
            require_cleanup(candidate.capabilities)
        finally:
            candidate_receipt = await candidate.close()
        report["candidateIdleCleanup"] = asdict(candidate_receipt)
        assert candidate_receipt.owned_work_stopped is True
        assert candidate_receipt.process_group_exited
        assert candidate_receipt.session_closed is True
    report["managedLaunchReady"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument(
        "--cleanup", action="store_true", help="Opt into candidate idle cleanup proof"
    )
    args = parser.parse_args()
    bridge, native = args.bridge.resolve(strict=True), args.native.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="flowfield-claude-acp-proof-") as scratch:
        report = asyncio.run(probe(bridge, native, Path(scratch), cleanup=args.cleanup))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
