"""Explicit native Claude CLI/Flowfield MCP guidance check; never ordinary tests/CI."""

import argparse
import asyncio
import json
import os
import socket
from pathlib import Path
from uuid import uuid4

import uvicorn

from flowfield.adapters.agent_mcp import ScopeServer
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_process import LocalProcess
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.guidance import Guidance, GuidanceChange

MODEL = "claude-sonnet-5-5"


async def proof(trial: Path, native: Path) -> dict:
    repo = trial / "project"
    repo.mkdir()
    word = uuid4().hex
    guidance = f"Fixture guidance word: {word}. Preserve repository files.\n"
    (repo / "CLAUDE.md").write_text(guidance)
    (repo / "AGENTS.md").write_text("Preserve the native CLAUDE.md. No edits are authorized.\n")
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "-m",
        "Fixture",
    )
    workspace = Workspace(trial / "state")
    workspace.setup_project(ProjectSetup(path=str(repo), id="standalone-proof"))
    owner = Guidance(workspace)
    owner.change(
        "standalone-proof",
        GuidanceChange(expected_revision=owner.get("standalone-proof").revision, action="install"),
    )
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "-m",
        "Adopted guidance",
    )
    initial = baseline(repo)
    app = create_app(data_dir=workspace.directory)
    server = ScopeServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            access_log=False,
            log_level="error",
            timeout_graceful_shutdown=5,
        )
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    config = trial / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "flowfield": {
                        "type": "http",
                        "url": f"http://127.0.0.1:{sock.getsockname()[1]}/mcp/",
                    }
                }
            }
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    process = None
    report = {
        "passed": False,
        "requestedModel": MODEL,
        "effort": "low",
        "personalServicesStarted": False,
    }
    try:
        async with asyncio.timeout(5):
            while not server.started:
                assert not serving.done()
                await asyncio.sleep(0.01)
        prompt = (
            "Read AGENTS.md and .agents/skills/flowfield-coordinator/SKILL.md. "
            "Follow its standalone orientation: identify the adopted project using its "
            ".flowfield/config.toml and Flowfield list_projects, then read its board with "
            "the explicit project ID. Read CLAUDE.md. Report the exact fixture guidance "
            "word, project ID, task count and any unclear guidance/tools. Do not edit "
            "files, change settings, create tasks or run commands."
        )
        process = await LocalProcess.start(
            [
                str(native),
                "--print",
                "--model",
                MODEL,
                "--effort",
                "low",
                "--output-format",
                "stream-json",
                "--verbose",
                "--no-session-persistence",
                "--strict-mcp-config",
                "--mcp-config",
                str(config),
                "--tools",
                "Read,ToolSearch",
                "--allowedTools",
                "Read",
                "mcp__flowfield__list_projects",
                "mcp__flowfield__get_board",
                "--max-budget-usd",
                "0.5",
                prompt,
            ],
            cwd=repo,
            env=dict(os.environ),
        )
        child = process.process
        assert child.stdout and child.stderr and child.stdin
        child.stdin.close()

        async def discard():
            while await child.stderr.read(16384):
                pass

        diagnostics = asyncio.create_task(discard())
        calls, models, result = [], [], None
        total = 0
        try:
            async with asyncio.timeout(120):
                async for line in child.stdout:
                    total += len(line)
                    if total > 1024 * 1024:
                        raise ValueError("Native output exceeded fixture bound")
                    event = json.loads(line)
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        models.append(event.get("model"))
                    elif event.get("type") == "assistant":
                        calls.extend(
                            item["name"]
                            for item in event.get("message", {}).get("content", [])
                            if item.get("type") == "tool_use"
                        )
                    elif event.get("type") == "result":
                        result = event
                assert await child.wait() == 0
                await diagnostics
            assert models == [MODEL] and result and not result.get("is_error")
            assert set(result.get("modelUsage", {})) == {MODEL}
            public = result.get("result", "")
            assert word in public and "standalone-proof" in public
            assert {"mcp__flowfield__list_projects", "mcp__flowfield__get_board"} <= set(calls)
            assert not workspace.tasks("standalone-proof")
            assert baseline(repo) == initial and not git(repo, "status", "--porcelain").strip()
            assert (repo / "CLAUDE.md").read_text() == guidance
            report.update(passed=True, observedModel=models[0], tools=calls, publicReply=public)
        finally:
            diagnostics.cancel()
            await asyncio.gather(diagnostics, return_exceptions=True)
    except Exception as error:
        report["failure"] = type(error).__name__
    finally:
        if process:
            report["processGroupExitConfirmed"] = await process.close()
        server.should_exit = True
        async with asyncio.timeout(10):
            await serving
        sock.close()
        if not report.get("processGroupExitConfirmed"):
            report["passed"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--trial-root", type=Path, required=True)
    parser.add_argument("--invoke-live", action="store_true", required=True)
    args = parser.parse_args()
    root = args.trial_root.resolve(strict=True)
    source = Path(__file__).resolve().parents[1]
    if source.parent in (root, *root.parents) or any(
        path in (root, *root.parents)
        for path in (Path("/tmp").resolve(), Path("/var/folders").resolve())
    ):
        parser.error("Use a dedicated trial root outside source and shared temporary directories")
    trial = root / f"h1-standalone-{uuid4().hex}"
    trial.mkdir(mode=0o700)
    report = asyncio.run(proof(trial, args.native.resolve(strict=True)))
    (trial / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"trial": str(trial), **report}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
