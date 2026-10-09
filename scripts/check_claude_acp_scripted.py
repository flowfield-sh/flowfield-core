"""Explicit full-bridge probe against a scripted SDK peer, never a live model.

The separate test tool has its own LocalProcess owner outside the bridge group.
Positive/uncertain receipts are checked against its actual observed lifetime.
"""

import argparse
import asyncio
import json
import shlex
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from acp.exceptions import RequestError
from acp.schema import HttpMcpServer, ImageContentBlock

from flowfield.adapters import claude_install
from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.claude_cleanup import CLAUDE_SHUTDOWN_TIMEOUTS, quiesce, require_cleanup
from flowfield.adapters.local_process import LocalProcess


async def probe(bridge: Path, scratch: Path, scenario: str, *, bundle: bool = False) -> dict:
    executable = scratch / "bridge"
    if bundle:
        executable = claude_install.install(
            scratch / "installed", bridge, claude_install.digest(bridge)
        )
    else:
        shutil.copy2(bridge, executable)
    fake = Path(__file__).resolve().parents[1] / "tests/fake_claude_cli.py"
    native = scratch / "native-fixture"
    native.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} "$@"\n'
    )
    native.chmod(0o700)
    home = scratch / "home"
    home.mkdir()
    record = scratch / "native.jsonl"
    env = {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "PATH": "",
        "CLAUDE_CODE_EXECUTABLE": str(native),
        "FLOWFIELD_TEST_SCENARIO": scenario,
        "FLOWFIELD_TEST_RECORD": str(record),
        "CLAUDE_AGENT_LOGS": str(scratch / "forbidden-logs"),
        "CLAUDE_AGENT_ACP_EXPERIMENTAL_V2": "1",
    }
    tool = None
    events = []
    permissions = []
    began = asyncio.Event()

    def event(value):
        events.append(value)
        if value.kind == "text":
            began.set()

    async def permission(value):
        permissions.append(value)
        return next(option[0] for option in value.options if option[2] == "allow_once")

    client = AcpSession(
        event,
        on_permission=permission,
        cleanup=quiesce,
        request_timeout=10,
        shutdown_timeouts=CLAUDE_SHUTDOWN_TIMEOUTS,
    )
    try:
        if scenario != "complete":
            tool = await LocalProcess.start(
                [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(120)"],
                cwd=scratch,
                env=env,
            )
            assert await asyncio.wait_for(tool.process.stdout.readline(), 5) == b"ready\n"
            env["FLOWFIELD_TEST_TOOL_PID"] = str(tool.process.pid)
        await client.start(
            [str(executable), "--flowfield-proof-cleanup"],
            cwd=scratch,
            env=env,
            mcp_servers=[
                HttpMcpServer(
                    name="flowfield_fixture",
                    type="http",
                    url="http://127.0.0.1:1/scoped-fixture",
                    headers=[],
                )
            ],
        )
        require_cleanup(client.capabilities)
        if bundle:
            assert client.capabilities["_meta"]["flowfield.sessionInfo"] == {
                "version": 1,
                "method": "_flowfield/sessionInfo",
            }
            info = await client.connection.ext_method(
                "flowfield/sessionInfo", {"sessionId": client.session_id}
            )
            assert set(info) == {"version", "sessionId", "model", "models"}
            assert info["model"] == "claude-sonnet-5-5"
            assert {item["id"] for item in info["models"]} == {"default", "sonnet"}
            for method, session_id in [
                ("flowfield/proofStatus", client.session_id),
                ("flowfield/sessionInfo", "another-session"),
            ]:
                try:
                    await client.connection.ext_method(method, {"sessionId": session_id})
                except RequestError:
                    pass
                else:
                    raise AssertionError("Private or foreign-session diagnostics were exposed")
        await client.select("model", "sonnet")
        await client.select("effort", "low")
        image = ImageContentBlock(
            type="image",
            mime_type="image/png",
            data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXxUAAAAASUVORK5CYII=",
        )
        prompt = asyncio.create_task(client.prompt("Synthetic conformance prompt", content=[image]))
        await asyncio.wait_for(began.wait(), 10)
        if scenario == "foreground":
            receipt = await client.close()
            await asyncio.wait_for(prompt, 5)
        else:
            assert await asyncio.wait_for(prompt, 10) == "end_turn"
            if scenario == "bridge-failure":
                client.process.kill()
                await asyncio.wait_for(client.process.wait(), 5)
            receipt = await client.close()
        assert receipt.process_group_exited
        positive = scenario not in {"refused", "bridge-failure"}
        assert receipt.owned_work_stopped is positive, asdict(receipt)
        if tool:
            if positive:
                await asyncio.wait_for(tool.process.wait(), 5)
                assert tool.process.returncode is not None
            else:
                assert tool.process.returncode is None
        assert "PRIVATE_FIXTURE_THOUGHT" not in json.dumps([asdict(item) for item in events])
        assert not (scratch / "forbidden-logs").exists()
        messages = [json.loads(line) for line in record.read_text().splitlines()]
        inputs = [item["input"] for item in messages if "input" in item]
        assert len(inputs) == 1
        assert any(block["type"] == "image" for block in inputs[0]["content"])
        assert any(item.get("control", {}).get("subtype") == "set_model" for item in messages)
        assert any(
            item.get("control", {}).get("subtype") == "apply_flag_settings" for item in messages
        )
        assert any("scoped-fixture" in json.dumps(item.get("mcpConfig", {})) for item in messages)
        if scenario == "complete":
            assert len(permissions) == 1
            reply = next(
                item["permissionResponse"] for item in messages if "permissionResponse" in item
            )
            assert reply["response"]["behavior"] == "allow"
        return {
            "scenario": scenario,
            "scriptedNative": True,
            "noLiveModel": True,
            "installedBundle": bundle,
            "publicEvents": len(events),
            "permissionRequests": len(permissions),
            "stop": asdict(receipt),
            "detachedTestToolExited": positive if tool else None,
        }
    finally:
        await client.close()
        if tool:
            assert await tool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    parser.add_argument("--bundle", action="store_true", help="Verify and install a runtime bundle")
    parser.add_argument(
        "--scenario",
        choices=["complete", "foreground", "background", "refused", "bridge-failure"],
        default="complete",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="flowfield-claude-scripted-") as scratch:
        result = asyncio.run(
            probe(
                args.bridge.resolve(strict=True), Path(scratch), args.scenario, bundle=args.bundle
            )
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
