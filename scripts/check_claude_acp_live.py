"""Explicit live native Claude/ACP proof; never included in ordinary checks.

Requires --invoke-live and an existing dedicated trial root outside source/shared
temporary directories. Uses native login without collecting or copying credentials.
"""

import argparse
import asyncio
import json
import os
import shlex
import shutil
import socket
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from acp.schema import HttpMcpServer
from mcp.server.fastmcp import FastMCP

from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.claude_cleanup import CLAUDE_SHUTDOWN_TIMEOUTS, quiesce, require_cleanup

MODEL = "claude-sonnet-5-5"


class ObservedSession(AcpSession):
    """Proof-only allowlisted native metadata, never assistant/private messages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.models = set()
        self.results = []
        self.snapshot_sizes = []
        self.native_tasks = set()
        self.native_task_types = set()

    async def ext_notification(self, method: str, params: dict) -> None:
        if method not in {"claude/sdkMessage", "_claude/sdkMessage"}:
            return
        if params.get("sessionId") != self.session_id:
            return
        message = params.get("message", {})
        if message.get("type") == "system" and message.get("subtype") == "init":
            self.models.add(message.get("model"))
        elif message.get("type") == "result":
            self.results.append(
                {
                    "subtype": message.get("subtype"),
                    "models": sorted(message.get("modelUsage", {})),
                    "costUsd": message.get("total_cost_usd"),
                    "durationMs": message.get("duration_ms"),
                    "permissionDenials": len(message.get("permission_denials", [])),
                }
            )
        elif (
            message.get("type") == "system" and message.get("subtype") == "background_tasks_changed"
        ):
            tasks = message.get("tasks", [])
            self.snapshot_sizes.append(len(tasks))
            self.native_tasks.update(task.get("task_id") for task in tasks)
            self.native_task_types.update(task.get("task_type") for task in tasks)


def metadata(scenario: str) -> dict:
    value = {
        "claudeCode": {
            "options": {
                "model": MODEL,
                "effort": "low",
                "maxTurns": 4,
                "maxBudgetUsd": 0.5,
                "persistSession": scenario == "continuity",
                "allowDangerouslySkipPermissions": False,
                "strictMcpConfig": True,
                "includeHookEvents": True,
                "tools": ["Read", "Bash"]
                if scenario in {"foreground", "background", "bridge-failure", "subagent"}
                else ["Read"],
            },
            "emitRawSDKMessages": [
                {"type": "system", "subtype": "init"},
                {"type": "result"},
                {"type": "system", "subtype": "background_tasks_changed"},
            ],
        }
    }
    if scenario == "subagent":
        value["claudeCode"]["options"]["tools"].append("Agent")
        value["claudeCode"]["options"]["agents"] = {
            "flowfield_fixture": {
                "description": "Run only the specified bounded test helper; no other work.",
                "prompt": (
                    "Use Bash for the exact helper command supplied in your assignment, then wait. "
                    "Do not read or edit files or use other tools."
                ),
                "tools": ["Bash"],
                "model": MODEL,
                "maxTurns": 2,
            }
        }
    return value


def command_projection(tool) -> str:
    value = tool.model_dump(by_alias=True).get("rawInput")
    if (
        isinstance(value, dict)
        and value.get("subagent_type") == "flowfield_fixture"
        and value.get("model") in {None, MODEL}
    ):
        return "fixture-agent"
    return str(value.get("command", "")) if isinstance(value, dict) else ""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # Observation only; never cleanup/recovery authority.
        return True
    except ProcessLookupError:
        return False


async def wait_for_marker(path: Path, nonce: str) -> int:
    async with asyncio.timeout(60):
        while not path.exists():
            await asyncio.sleep(0.05)
    value = json.loads(path.read_text())
    assert value["nonce"] == nonce and type(value["pid"]) is int and value["pid"] > 1
    return value["pid"]


async def verify_model(client: ObservedSession) -> dict:
    assert client.connection and client.session_id
    status = await client.connection.ext_method(
        "flowfield/proofStatus", {"sessionId": client.session_id}
    )
    assert status.get("sessionId") == client.session_id
    assert status.get("model") == MODEL, "Native model must be confirmed before prompt dispatch"
    return status


@asynccontextmanager
async def mcp_fixture(nonce: str, calls: list):
    mcp = FastMCP(
        "flowfield_fixture", stateless_http=True, json_response=True, log_level="CRITICAL"
    )

    @mcp.tool()
    def fixture_identity() -> str:
        """Read the isolated trial's identity code."""
        calls.append("identity")
        return nonce

    app = mcp.streamable_http_app()

    async def scoped(scope, receive, send):
        if (
            scope["type"] == "http"
            and dict(scope.get("headers", [])).get(b"x-flowfield-proof") != nonce.encode()
        ):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"Wrong fixture scope"})
            return
        await app(scope, receive, send)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
    server = uvicorn.Server(
        uvicorn.Config(scoped, log_level="critical", access_log=False, loop="asyncio")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient() as client:
            assert (await client.get(endpoint)).status_code == 401
        yield HttpMcpServer(
            name="flowfield_fixture",
            type="http",
            url=endpoint,
            headers=[{"name": "X-Flowfield-Proof", "value": nonce}],
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


async def probe(bridge: Path, native: Path, trial: Path, scenario: str = "smoke") -> dict:
    executable = trial / "bridge"
    shutil.copy2(bridge, executable)
    events = []
    command = ""
    permissions = []
    tool_pid = None
    helper = trial / "bounded_tool.py"
    helper.write_text(
        "import json, os, time\nfrom pathlib import Path\n"
        f"Path('ready.json').write_text(json.dumps({{'pid':os.getpid(),'nonce':{trial.name!r}}}))\n"
        "time.sleep(90)\nPath('bounded-exit').write_text('expired')\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(helper))}"

    async def permission(request):
        allowed = (
            request.details.strip() == command
            or (scenario == "mcp" and "fixture_identity" in request.title)
            or (scenario == "subagent" and request.details.strip() == "fixture-agent")
        )
        permissions.append({"fixtureCommandMatched": allowed})
        return next(
            (value[0] for value in request.options if allowed and value[2] == "allow_once"), None
        )

    def create():
        return ObservedSession(
            events.append,
            on_permission=permission,
            permission_projection=command_projection,
            request_timeout=45,
            cleanup=quiesce,
            shutdown_timeouts=CLAUDE_SHUTDOWN_TIMEOUTS,
        )

    client = create()
    env = {
        **os.environ,
        "CLAUDE_CODE_EXECUTABLE": str(native),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    report = {
        "nativeClient": True,
        "liveModel": True,
        "requestedModel": MODEL,
        "scenario": scenario,
        "passed": False,
    }
    stack = AsyncExitStack()
    calls = []
    servers = []
    try:
        if scenario == "hook":
            (trial / ".claude").mkdir()
            (trial / ".claude/settings.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {"hooks": [{"type": "command", "command": command, "timeout": 95}]}
                            ],
                        }
                    }
                )
            )
        if scenario == "mcp":
            servers.append(await stack.enter_async_context(mcp_fixture(trial.name, calls)))
        await client.start(
            [str(executable), "--flowfield-proof-cleanup"],
            cwd=trial,
            env=env,
            mcp_servers=servers,
            session_metadata=metadata(scenario),
        )
        require_cleanup(client.capabilities)
        status = await verify_model(client)
        report.update(prePromptModel=status["model"], nativeAccountRoute=status["account"])
        if scenario in {"foreground", "background", "bridge-failure", "subagent", "hook"}:
            background = scenario in {"background", "bridge-failure", "subagent"}
            instruction = (
                f"Use Bash exactly once to run this exact command: {command}\n"
                f"Set run_in_background to {str(background).lower()}. Do not modify the command. "
                "Do not read files or use any other tool. "
                + (
                    "Then reply FLOWFIELD_BACKGROUND_READY without waiting for completion."
                    if background
                    else "Wait for the command."
                )
            )
            if scenario == "subagent":
                instruction = (
                    "Use Agent once to launch the flowfield_fixture subagent "
                    "with run_in_background true. "
                    f"Assign it to run this exact Bash command in foreground and wait: {command}. "
                    "Do not specify another model or run Bash yourself. "
                    "Then reply FLOWFIELD_BACKGROUND_READY without waiting."
                )
            elif scenario == "hook":
                instruction = "Reply exactly FLOWFIELD_SONNET_READY. Do not use tools."
            prompt = asyncio.create_task(client.prompt(instruction))
            tool_pid = await wait_for_marker(trial / "ready.json", trial.name)
            assert alive(tool_pid), "The native tool must be live before Stop"
            report["nativeToolAliveBeforeStop"] = True
            report["nativeToolOutsideBridgeGroup"] = os.getpgid(tool_pid) != client.process.pid
            if background:
                if scenario != "subagent":
                    assert await asyncio.wait_for(prompt, 60) == "end_turn"
                else:
                    report["promptPendingAtStop"] = not prompt.done()
                    assert "local_agent" in client.native_task_types
                report["backgroundMembershipObserved"] = bool(client.native_tasks)
                assert client.native_tasks, "Missing native background membership"
                assert alive(tool_pid), "Background must still be live before Stop"
            if scenario == "bridge-failure":
                client.process.kill()  # Exact retained bridge owner; no PID recovery.
                await asyncio.wait_for(client.process.wait(), 5)
            receipt = await client.close()
            await asyncio.wait_for(prompt, 10)
            await asyncio.sleep(0.1)
            report["nativeToolGoneAfterStop"] = not alive(tool_pid)
            if receipt.owned_work_stopped is True or scenario not in {"bridge-failure", "hook"}:
                assert not alive(tool_pid), "Native tool survived confirmed Stop"
            else:
                assert scenario == "hook" or receipt.owned_work_stopped is False, (
                    "Bridge death cannot confirm native work"
                )
        else:
            instruction = "Reply with exactly FLOWFIELD_SONNET_READY. Do not use tools."
            if scenario == "continuity":
                instruction = (
                    f"Remember the unresolved requirement code {trial.name}. "
                    "Reply exactly FLOWFIELD_SONNET_READY. Do not use tools."
                )
            elif scenario == "mcp":
                instruction = (
                    "Call the flowfield_fixture fixture_identity MCP tool exactly once, "
                    "then reply with only the identity code it returned. Do not use other tools."
                )
            async with asyncio.timeout(120):
                result = await client.prompt(instruction)
            text = "".join(event.data["text"] for event in events if event.kind == "text")
            expected = trial.name if scenario == "mcp" else "FLOWFIELD_SONNET_READY"
            assert text.strip() == expected, "Unexpected fixture reply"
            assert result == "end_turn"
            if scenario == "mcp":
                assert calls == ["identity"], "Missing/duplicate scoped MCP call"
                report["scopedMcpCallVerified"] = True
                report["wrongScopeRejected"] = True
            if scenario == "continuity":
                binding = client.session_id
                first_models, first_results = set(client.models), list(client.results)
                report["firstStop"] = asdict(await client.close())
                assert report["firstStop"]["owned_work_stopped"] is True
                events.clear()
                client = create()
                await client.start(
                    [str(executable), "--flowfield-proof-cleanup"],
                    cwd=trial,
                    env=env,
                    mcp_servers=[],
                    session_metadata=metadata(scenario),
                    resume_session_id=binding,
                )
                await verify_model(client)
                async with asyncio.timeout(120):
                    result = await client.prompt(
                        "What is the unresolved requirement code from our prior exchange? "
                        "Reply with only that code. Do not use tools."
                    )
                text = "".join(event.data["text"] for event in events if event.kind == "text")
                assert text.strip() == trial.name and result == "end_turn", (
                    "Native history was not restored"
                )
                report["historyRestored"] = True
                client.models.update(first_models)
                client.results[:0] = first_results
        assert scenario == "hook" or client.models == {MODEL}, "Unexpected native model identity"
        assert (scenario == "hook" or client.results) and all(
            item["models"] in [[], [MODEL]] if scenario == "hook" else item["models"] == [MODEL]
            for item in client.results
        ), "Unexpected result model"
        report["passed"] = True
    except Exception as error:
        # Native RequestError messages/data can contain private host/account material.
        report["failureType"] = type(error).__name__
        report["failureCode"] = getattr(error, "code", None)
    finally:
        receipt = await client.close()
        report["stop"] = asdict(receipt)
        report["observedModels"] = sorted(client.models)
        report["results"] = client.results
        report["publicEventKinds"] = sorted({event.kind for event in events})
        report["permissions"] = permissions
        report["nativeTaskTypes"] = sorted(client.native_task_types)
        await stack.aclose()
        if tool_pid and alive(tool_pid):
            # Observe the bounded helper expiring; never terminate by this marker's PID.
            async with asyncio.timeout(100):
                while alive(tool_pid):
                    await asyncio.sleep(0.1)
            report["boundedToolEventuallyExited"] = True
    positive = scenario != "bridge-failure"
    if scenario == "hook":
        # A truthful uncertain hook receipt is useful negative evidence, never acceptance.
        report["hookCleanupConfirmed"] = receipt.owned_work_stopped is True
    if receipt.owned_work_stopped is not positive or not receipt.process_group_exited:
        report["passed"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--trial-root", type=Path, required=True)
    parser.add_argument("--invoke-live", action="store_true", required=True)
    parser.add_argument(
        "--scenario",
        choices=[
            "smoke",
            "foreground",
            "background",
            "bridge-failure",
            "continuity",
            "mcp",
            "subagent",
            "hook",
        ],
        default="smoke",
    )
    args = parser.parse_args()
    root = args.trial_root.resolve(strict=True)
    source = Path(__file__).resolve().parents[1]
    if source.parent in (root, *root.parents) or any(
        path in (root, *root.parents)
        for path in [Path("/tmp").resolve(), Path("/var/folders").resolve()]
    ):
        parser.error("Use a dedicated trial root outside source and shared temporary directories")
    trial = root / f"h1-{args.scenario}-{uuid4().hex}"
    trial.mkdir(mode=0o700)
    report = asyncio.run(
        probe(
            args.bridge.resolve(strict=True), args.native.resolve(strict=True), trial, args.scenario
        )
    )
    (trial / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"trial": str(trial), **report}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
