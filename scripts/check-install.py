"""Exercise an installed distribution with no development tools or test dependencies."""

import asyncio
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call_mcp(base: str, name: str, arguments: dict) -> dict:
    async with (
        streamable_http_client(base + "/mcp/") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
        assert not result.isError, result
        assert result.structuredContent is not None
        return result.structuredContent


def check_storage(executable: str, cwd: str, env: dict[str, str]) -> None:
    from flowfield.application import Workspace
    from flowfield.schema import SCHEMA_VERSION

    directory = (Path(cwd) / "storage-state").resolve()

    def command() -> dict:
        return json.loads(
            subprocess.check_output(
                [executable, "--data-dir", str(directory), "storage", "status", "--json"],
                cwd=cwd,
                env=env,
                text=True,
            )
        )

    assert command()["schema_version"] is None
    assert not directory.exists()
    Workspace(directory)
    assert command()["schema_version"] == SCHEMA_VERSION
    Workspace(directory)
    assert command()["schema_version"] == SCHEMA_VERSION


async def check_mcp(base: str, task: dict, workspace_id: str) -> None:
    async with (
        streamable_http_client(base + "/mcp/") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        from flowfield.guidance import template

        assert initialized.instructions == template("mcp-instructions.md").strip()
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert {"ask_question", "follow_up_question", "retract_answer", "reopen_task"}.isdisjoint(
            tools
        )
        assert {
            "get_task",
            "get_workspace_workers",
            "get_runs",
            "review_result",
            "set_workspace_queue",
        } <= tools
        assert {"get_workers", "configure_workers", "set_queue"}.isdisjoint(tools)
        assert {"get_project_guidance", "update_project_guidance"} <= tools
        assert {
            "get_task_conversation",
            "get_conversation_source",
            "get_task_input",
            "reply_to_task",
            "cancel_task_reply",
        } <= tools
        assert {"prepare_inspection", "get_inspection", "configure_inspection"} <= tools
        assert "retry_result_delivery" in tools
        assert {
            "list_documents",
            "get_document",
            "get_document_text",
            "edit_document",
            "restore_document",
        } <= tools
        document = await session.call_tool(
            "get_document", {"workspace_id": workspace_id, "document_id": "guidance"}
        )
        assert (
            not document.isError
            and document.structuredContent["body"] == "Installed shared guidance"
        )
        assert "create_project" not in tools
        result = await session.call_tool(
            "get_task", {"workspace_id": workspace_id, "task_id": "T-2"}
        )
        assert not result.isError and result.structuredContent == task
        thread = await session.call_tool(
            "get_task_conversation", {"workspace_id": workspace_id, "task_id": "T-2"}
        )
        assert not thread.isError and thread.structuredContent["items"]
        activity = await session.call_tool(
            "list_activity",
            {
                "workspace_id": workspace_id,
                "task_id": "T-2",
                "current_only": True,
            },
        )
        assert not activity.isError
        assert activity.structuredContent["items"][0]["id"] == "installed-handoff"


async def check_native_runtime(directory: Path) -> None:
    import flowfield.adapters.pi_agent as pi_module
    from flowfield.adapters.claude_agent import ClaudeAgent
    from flowfield.adapters.pi_agent import PiAgent
    from flowfield.agent_models import AgentChoice
    from flowfield.harness_models import HarnessRegistration

    assert Path(pi_module.__file__).with_name("pi_extension.js").is_file()
    pi_native = directory / "pi-cli"
    pi_native.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(Path(__file__).with_name("pi-fixture.py")))
        + ' "$@"\n'
    )
    pi_native.chmod(0o700)
    pi_config = directory / "pi-config"
    pi_config.mkdir()
    pi_agent = PiAgent(
        directory,
        directory,
        os.environ,
        registration=HarnessRegistration(
            harness="pi",
            executable=str(pi_native),
            config_directory=str(pi_config),
        ),
    )
    try:
        await pi_agent.start([])
        await pi_agent.configure(
            AgentChoice(harness="pi", model="first/shared-model", effort="low", mode="full-access")
        )
        assert await pi_agent.prompt("Scripted Pi install check", None) == {"status": "completed"}
    finally:
        assert await pi_agent.stop()

    native = directory / "native-cli"
    native.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(Path(__file__).with_name("native-fixture.py")))
        + ' "$@"\n'
    )
    native.chmod(0o700)
    config = directory / "native-config"
    config.mkdir()
    choice = AgentChoice(
        harness="claude-code", model="claude-sonnet-5-5", effort="low", mode="default"
    )
    agent = ClaudeAgent(
        directory,
        {
            "PATH": str(Path(sys.executable).parent),
            "HOME": str(directory),
            "FLOWFIELD_TEST_SCENARIO": "complete",
            "FLOWFIELD_TEST_RECORD": str(directory / "native.jsonl"),
        },
        registration=HarnessRegistration(
            harness="claude-code", executable=str(native), config_directory=str(config)
        ),
        choice=choice,
    )
    member = directory / "member-repository"
    member.mkdir()
    agent.project_directories = (member,)
    try:
        await agent.start([], persistent=True)
        await agent.configure(choice)
        calls = [json.loads(line) for line in (directory / "native.jsonl").read_text().splitlines()]
        args = next(item["args"] for item in calls if "args" in item)
        assert args[args.index("--add-dir") + 1] == str(member)
        assert await agent.prompt("Scripted install fixture only", None) == {"status": "completed"}
    finally:
        assert await agent.stop()


def main() -> None:
    executable = str(Path(sysconfig.get_path("scripts")) / "flowfield")
    env = {**os.environ, "PATH": sysconfig.get_path("scripts"), "FLOWFIELD_UPDATE_CHECKS": "0"}
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="flowfield-installed-") as cwd:
        check_storage(executable, cwd, env)
        state = Path(cwd) / "state"
        env["FLOWFIELD_DATA_DIR"] = str(state)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env["FLOWFIELD_PORT"] = str(port)
        base = f"http://127.0.0.1:{port}"
        opener = build_opener(ProxyHandler({}))

        def command(*args: str) -> str:
            return subprocess.check_output([executable, *args], cwd=cwd, env=env, text=True).strip()

        def agent(name: str, **arguments: object) -> dict:
            return asyncio.run(call_mcp(base, name, arguments))

        command("--help")
        for retired in ("workspace", "project", "task", "inbox"):
            rejected = subprocess.run(
                [executable, retired, "--help"], cwd=cwd, env=env, capture_output=True
            )
            assert rejected.returncode == 2
        version = command("--version")
        if expected := env.get("EXPECTED_VERSION"):
            assert version == expected
            import importlib.metadata

            import flowfield

            assert importlib.metadata.version("flowfield-core") == expected
            assert Path(flowfield.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        assert json.loads(command("version", "--json")) == {"version": version}
        from flowfield.adapters.claude_runtime import executable as sdk_executable

        assert sdk_executable().is_file()
        asyncio.run(check_native_runtime(Path(cwd)))
        assert "harness install" not in command("harness", "--help")
        assert not state.exists()

        def read(path: str) -> bytes:
            with opener.open(base + path, timeout=2) as response:
                assert response.status == 200
                return response.read()

        def write(path: str, body: dict, *, method: str = "POST") -> dict:
            request = Request(
                base + path,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method=method,
            )
            with opener.open(request, timeout=2) as response:
                return json.load(response)

        original = Path(cwd) / "AGENTS.md"
        original.write_text("Preserve existing instructions.\n")
        plan = Path(cwd) / "plan.md"
        plan.write_text("Export filtered rows.\n")
        for attempt in range(2):
            with tempfile.TemporaryFile() as log:
                process = subprocess.Popen(
                    [executable, "serve"], cwd=cwd, env=env, stdout=log, stderr=log
                )
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("Installed server exited during startup")
                        try:
                            health = json.loads(read("/api/health"))
                            break
                        except URLError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(0.1)
                    assert health == {"status": "ok", "version": version}
                    assert not (state / "access-token").exists()
                    if attempt == 0:
                        assert json.loads(read("/api/announcements/settings")) == {
                            "system_enabled": False,
                            "work_enabled": True,
                            "updates_enabled": True,
                        }
                        preferences = write(
                            "/api/announcements/settings",
                            {"work_enabled": False},
                            method="PATCH",
                        )
                        assert preferences == {
                            "system_enabled": False,
                            "work_enabled": False,
                            "updates_enabled": True,
                        }
                        assert json.loads(read("/api/projects")) == []
                        page = read("/").decode()
                        assert "Flowfield" in page
                        assert read("/settings/harnesses").decode() == page
                        assert read("/settings/notifications").decode() == page
                        assert read("/settings/updates").decode() == page
                        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
                        assert any(asset.endswith(".js") for asset in assets)
                        assert any(asset.endswith(".css") for asset in assets)
                        assert "2023 shadcn" in read("/assets/shadcn-license.txt").decode()
                        licenses = read("/assets/third-party-licenses.md").decode()
                        for dependency in ["react", "react-dom", "lucide-react", "refractor"]:
                            assert f"## {dependency} - " in licenses
                        assert "Tailwind Labs" in read("/assets/tailwindcss-license.txt").decode()
                        assert (
                            "Anton Korzunov"
                            in read("/assets/react-remove-scroll-bar-license.txt").decode()
                        )
                        for asset in assets:
                            assert read(asset), asset
                        for path, status in [("/api/unknown", 404)]:
                            try:
                                read(path)
                            except HTTPError as error:
                                assert error.code == status
                            else:
                                raise AssertionError(f"Expected {status} for {path}")
                        assert json.loads(read("/api/workspaces")) == []
                        space = agent(
                            "create_workspace",
                            request={
                                "name": "Installed workspace",
                                **{
                                    role: {"harness": "codex", "model": "install-fixture"}
                                    for role in ("coordinator", "worker", "reviewer")
                                },
                            },
                        )
                        scope = {"workspace_id": space["id"]}
                        assert agent("get_coordinator_history", **scope)["items"] == []
                        assert not agent("get_workspace_workers", **scope)["enabled"]
                        task = agent(
                            "create_task",
                            **scope,
                            task={"title": "Installed task", "body": "Shared outcome"},
                        )
                        assert agent("list_tasks", **scope)["items"][0]["id"] == task["id"]
                        assert (
                            agent(
                                "get_text",
                                **scope,
                                resource="task",
                                identity=task["key"],
                                field="body",
                                revision=1,
                            )["text"]
                            == "Shared outcome"
                        )
                        initialized = agent(
                            "adopt_workspace_project",
                            **scope,
                            project={"path": cwd, "id": "harbor", "name": "Harbor"},
                        )
                        assert (
                            agent("adopt_workspace_project", **scope, project={"path": cwd})
                            == initialized
                        )
                        document_path = f"/api/workspaces/{space['id']}/documents/guidance"
                        assert json.loads(read(document_path))["body"] == ""
                        write(
                            document_path,
                            {"expected_revision": 1, "body": "Installed shared guidance"},
                            method="PUT",
                        )
                        write(
                            document_path,
                            {"expected_revision": 2, "body": "Later guidance"},
                            method="PUT",
                        )
                        restored = write(
                            document_path + "/restore", {"expected_revision": 3, "revision": 2}
                        )
                        assert restored["revision"] == 4 and restored["restored_from"] == 2
                        assert (Path(cwd) / ".flowfield/config.toml").is_file()
                        section = command("integration", "guidance", "export", "--part", "agents")
                        assert ".agents/skills/flowfield-coordinator/SKILL.md" in section
                        original_text = original.read_text()
                        preview = json.loads(
                            command("integration", "guidance", "preview", "--json")
                        )
                        assert preview["status"] == "available"
                        command("integration", "guidance", "install", "--json")
                        installed = (Path(cwd) / "AGENTS.md").read_bytes()
                        command("integration", "guidance", "install", "--json")
                        assert (Path(cwd) / "AGENTS.md").read_bytes() == installed
                        assert (
                            Path(cwd) / ".agents/skills/flowfield-coordinator/SKILL.md"
                        ).is_file()
                        command("integration", "guidance", "remove", "--json")
                        assert (Path(cwd) / "AGENTS.md").read_text() == original_text
                        assert not (
                            Path(cwd) / ".agents/skills/flowfield-coordinator/SKILL.md"
                        ).exists()
                        created = agent(
                            "create_task",
                            **scope,
                            task={"id": "export", "title": "CSV export", "body": plan.read_text()},
                        )
                        assert created["status"] == "backlog"
                        plan.write_text("Export filtered rows, preserving row order.\n")
                        agent(
                            "edit_task",
                            **scope,
                            task_id="export",
                            changes={
                                "expected_revision": 1,
                                "title": "CSV export",
                                "body": plan.read_text(),
                            },
                        )
                        agent(
                            "prioritize_task",
                            **scope,
                            task_id="export",
                            priority={"expected_revision": 2, "status": "up_next"},
                        )
                        agent(
                            "add_activity",
                            **scope,
                            entry={"task_id": "T-2", "body": "Serializer verified."},
                        )
                        agent(
                            "add_activity",
                            **scope,
                            entry={
                                "task_id": "T-2",
                                "kind": "handoff",
                                "expected_task_revision": 3,
                                "supersedes": None,
                                "id": "installed-handoff",
                                "body": (
                                    "Package-check fixture; no code executed. Next: inspect task."
                                ),
                            },
                        )
                    task = agent("get_task", **scope, task_id="T-2")
                    assert task["key"] == "T-2"
                    assert task["handoff"]["id"] == "installed-handoff"
                    assert task["handoff"]["needs_recheck"] is False
                    activity = agent("list_activity", **scope, task_id="T-2", current_only=True)
                    assert activity["items"][0]["id"] == "installed-handoff"
                    assert (
                        json.loads(read(f"/api/context/workspaces/{space['id']}/tasks/T-2")) == task
                    )
                    assert task["revision"] == 3 and task["status"] == "up_next"
                    assert "revisions" not in task
                    assert len(agent("list_task_revisions", **scope, task_id="T-2")["items"]) == 3
                    board = agent("get_board", **scope)
                    assert board["columns"][1]["tasks"][0]["key"] == task["key"]
                    assert board["recommendation"]["action"] == "work"
                    assert task["readiness"] == "ready"
                    assert "publication_status" not in task
                    workers = agent("get_workspace_workers", **scope)
                    assert workers["selection"]["model"] == "install-fixture"
                    assert workers["max_parallel"] == 5 and workers["enabled"] is False
                    assert agent("get_runs", **scope)["items"] == []
                    assert agent("get_reviewer_settings", **scope)["effective"]["enabled"]
                    assert (
                        agent("get_coordinator_settings", **scope)["selection"]["model"]
                        == "install-fixture"
                    )
                    assert agent("get_worker_occupancy", **scope) == {"active": 0, "uncertain": 0}
                    assert board["observed_at"]
                    assert b"<html" in read(f"/workspaces/{space['id']}/tasks/T-2").lower()
                    asyncio.run(check_mcp(base, task, space["id"]))
                    assert original.read_text() == "Preserve existing instructions.\n"
                    assert (state / "workspace.sqlite3").is_file()
                    assert (state / "artifacts").is_dir()
                    updates = json.loads(command("update", "status", "--json"))
                    assert updates["automatic"] is False and updates["last_attempt"] is None
                    assert json.loads(read("/api/updates")) == updates
                    assert json.loads(read("/api/announcements/settings")) == preferences
                except BaseException:
                    log.seek(0)
                    sys.stderr.write(log.read().decode())
                    raise
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    print(
        "Installed package: project setup, CLI/API/MCP/UI, "
        "task board, briefing/handoff, activity, revisions, restart, "
        "offline storage inspection, announcement preferences and update status passed."
    )


if __name__ == "__main__":
    main()
