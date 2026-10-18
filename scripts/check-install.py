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
            if args and args[0] in {"task", "status"} and "--workspace" not in args:
                args = (*args, "--workspace", space["id"])
            return subprocess.check_output([executable, *args], cwd=cwd, env=env, text=True).strip()

        command("--help")
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
                        space = json.loads(
                            command("workspace", "create", "Installed workspace", "--json")
                        )
                        assert (
                            json.loads(
                                command(
                                    "workspace",
                                    "coordinator-history",
                                    "--workspace",
                                    space["id"],
                                    "--json",
                                )
                            )["items"]
                            == []
                        )
                        assert (
                            json.loads(
                                command(
                                    "workspace",
                                    "workers",
                                    "show",
                                    "--workspace",
                                    space["id"],
                                    "--json",
                                )
                            )["enabled"]
                            is False
                        )
                        task = json.loads(
                            command(
                                "task",
                                "create",
                                "--workspace",
                                space["id"],
                                "--title",
                                "Installed task",
                                "--body",
                                "Shared outcome",
                                "--json",
                            )
                        )
                        assert (
                            json.loads(
                                command("task", "list", "--workspace", space["id"], "--json")
                            )["items"][0]["id"]
                            == task["id"]
                        )
                        assert (
                            json.loads(
                                command(
                                    "task",
                                    "text",
                                    task["key"],
                                    "--workspace",
                                    space["id"],
                                    "--revision",
                                    "1",
                                    "--json",
                                )
                            )["text"]
                            == "Shared outcome"
                        )
                        initialized = command(
                            "project",
                            "init",
                            "--workspace",
                            space["id"],
                            "--id",
                            "harbor",
                            "--name",
                            "Harbor",
                            "--json",
                        )
                        assert (
                            command("project", "init", "--workspace", space["id"], "--json")
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
                        section = command("project", "guidance", "export", "--part", "agents")
                        assert ".agents/skills/flowfield-coordinator/SKILL.md" in section
                        original_text = original.read_text()
                        preview = json.loads(command("project", "guidance", "preview", "--json"))
                        assert preview["status"] == "available"
                        command("project", "guidance", "install", "--json")
                        installed = (Path(cwd) / "AGENTS.md").read_bytes()
                        command("project", "guidance", "install", "--json")
                        assert (Path(cwd) / "AGENTS.md").read_bytes() == installed
                        assert (
                            Path(cwd) / ".agents/skills/flowfield-coordinator/SKILL.md"
                        ).is_file()
                        command("project", "guidance", "remove", "--json")
                        assert (Path(cwd) / "AGENTS.md").read_text() == original_text
                        assert not (
                            Path(cwd) / ".agents/skills/flowfield-coordinator/SKILL.md"
                        ).exists()
                        created = json.loads(
                            command(
                                "task",
                                "create",
                                "--id",
                                "export",
                                "--title",
                                "CSV export",
                                "--body-file",
                                str(plan),
                                "--json",
                            )
                        )
                        assert created["status"] == "backlog"
                        plan.write_text("Export filtered rows, preserving row order.\n")
                        command(
                            "task",
                            "edit",
                            "export",
                            "--expected-revision",
                            "1",
                            "--title",
                            "CSV export",
                            "--body-file",
                            str(plan),
                            "--json",
                        )
                        command(
                            "task",
                            "prioritize",
                            "export",
                            "up-next",
                            "--expected-revision",
                            "2",
                            "--json",
                        )
                        command("task", "note", "T-2", "--body", "Serializer verified.", "--json")
                        command(
                            "task",
                            "handoff",
                            "T-2",
                            "--expected-revision",
                            "3",
                            "--supersedes",
                            "none",
                            "--id",
                            "installed-handoff",
                            "--body",
                            "Package-check fixture; no code executed. Next: inspect task.",
                            "--json",
                        )
                    task = json.loads(command("task", "show", "T-2", "--json"))
                    assert task["key"] == "T-2"
                    assert task["handoff"]["id"] == "installed-handoff"
                    assert task["handoff"]["needs_recheck"] is False
                    activity = json.loads(command("task", "activity", "T-2", "--current", "--json"))
                    assert activity["items"][0]["id"] == "installed-handoff"
                    assert (
                        json.loads(read(f"/api/context/workspaces/{space['id']}/tasks/T-2")) == task
                    )
                    assert task["revision"] == 3
                    assert task["status"] == "up_next"
                    assert "revisions" not in task
                    assert (
                        len(json.loads(command("task", "revisions", "T-2", "--json"))["items"]) == 3
                    )
                    board = json.loads(command("status", "--json"))
                    assert board["columns"][1]["tasks"][0]["key"] == task["key"]
                    assert board["recommendation"]["action"] == "publish"
                    workers = json.loads(
                        command(
                            "workspace", "workers", "show", "--workspace", space["id"], "--json"
                        )
                    )
                    assert workers["selection"] is None and workers["max_parallel"] == 1
                    assert workers["enabled"] is False
                    assert json.loads(command("task", "runs", "list", "--json"))["items"] == []
                    assert board["observed_at"]
                    assert b"<html" in read(f"/workspaces/{space['id']}/tasks/T-2").lower()
                    asyncio.run(check_mcp(base, task, json.loads(read("/api/workspaces"))[0]["id"]))
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
