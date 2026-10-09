"""Exercise an installed distribution with no development tools or test dependencies."""

import asyncio
import json
import os
import re
import socket
import sqlite3
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
    from flowfield import storage
    from flowfield.application import SCHEMA, Workspace
    from flowfield.migrations import BASELINE_VERSION, current_version

    directory = (Path(cwd) / "storage-state").resolve()

    def command(*args: str) -> dict | list:
        return json.loads(
            subprocess.check_output(
                [executable, "--data-dir", str(directory), "storage", *args, "--json"],
                cwd=cwd,
                env=env,
                text=True,
            )
        )

    assert command("status")["schema_version"] is None
    assert not directory.exists()
    # Materialize the frozen public baseline using only installed package content.
    # New-state initialization applies current migrations and has no old-schema backup.
    directory.mkdir()
    with sqlite3.connect(directory / storage.DATABASE) as db:
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version={BASELINE_VERSION}")
    assert command("status")["schema_version"] == BASELINE_VERSION
    with storage.maintenance(directory):
        storage.snapshot(directory, reason="recovery")
    saved = command("backups")[0]
    assert saved["schema_version"] == BASELINE_VERSION
    Workspace(directory)
    assert command("status")["migration_required"] is False
    assert command("status")["schema_version"] == current_version()
    assert command("restore", saved["id"], "--confirm")["restored"] == saved["id"]
    assert command("status")["schema_version"] == BASELINE_VERSION
    Workspace(directory)
    assert command("status")["schema_version"] == current_version()


async def check_mcp(base: str, task: dict) -> None:
    async with (
        streamable_http_client(base + "/mcp/") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        from flowfield.guidance import template

        assert initialized.instructions == template("mcp-instructions.md").strip()
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert {"get_task", "get_workers", "get_runs", "review_result", "set_queue"} <= tools
        assert {"get_project_guidance", "update_project_guidance"} <= tools
        assert {
            "get_task_conversation",
            "get_conversation_source",
            "get_task_stages",
            "update_task_stages",
            "get_task_input",
            "reply_to_task",
            "cancel_task_reply",
        } <= tools
        assert {"prepare_inspection", "get_inspection", "configure_inspection"} <= tools
        assert "retry_result_delivery" in tools
        assert "create_project" not in tools
        result = await session.call_tool("get_task", {"project_id": "harbor", "task_id": "HAR-1"})
        assert not result.isError and result.structuredContent == task
        stages = await session.call_tool(
            "get_task_stages", {"project_id": "harbor", "task_id": "HAR-1"}
        )
        assert not stages.isError and stages.structuredContent["revision"] == 2
        thread = await session.call_tool(
            "get_task_conversation", {"project_id": "harbor", "task_id": "HAR-1"}
        )
        assert not thread.isError and thread.structuredContent["items"]
        activity = await session.call_tool(
            "list_activity",
            {
                "project_id": "harbor",
                "task_id": "HAR-1",
                "current_only": True,
            },
        )
        assert not activity.isError
        assert activity.structuredContent["items"][0]["id"] == "installed-handoff"


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

        command("--help")
        version = command("--version")
        if expected := env.get("EXPECTED_VERSION"):
            assert version == expected
            import importlib.metadata

            import flowfield

            assert importlib.metadata.version("flowfield-core") == expected
            assert Path(flowfield.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        assert json.loads(command("version", "--json")) == {"version": version}
        for harness in ("codex", "claude-code"):
            runtime = subprocess.run(
                [executable, "harness", "status", harness, "--json"],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert runtime.returncode == 1
            assert json.loads(runtime.stderr)["error"]["code"] == "bridge_missing"
        assert not state.exists()

        def read(path: str) -> bytes:
            with opener.open(base + path, timeout=2) as response:
                assert response.status == 200
                return response.read()

        def write(path: str, body: dict) -> dict:
            request = Request(
                base + path,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
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
                        write(
                            "/api/notifications/operations",
                            {
                                "key": "installed-check",
                                "title": "Package verification",
                                "message": "This notice survives the service restart.",
                            },
                        )
                        assert json.loads(read("/api/projects")) == []
                        page = read("/").decode()
                        assert "Flowfield" in page
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
                        initialized = command(
                            "project", "init", "--id", "harbor", "--name", "Harbor", "--json"
                        )
                        assert command("project", "init", "--json") == initialized
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
                        stage_plan = json.loads(command("task", "stages", "export", "--json"))
                        assert stage_plan["revision"] == 1 and len(stage_plan["stages"]) == 1
                        stage_file = Path(cwd) / "stages.json"
                        stage_file.write_text(
                            json.dumps(
                                {
                                    "expected_revision": stage_plan["revision"],
                                    "stages": stage_plan["stages"],
                                    "reason": "Clarified export scope",
                                }
                            )
                        )
                        plan.write_text("Export filtered rows, preserving row order.\n")
                        command(
                            "task",
                            "edit",
                            "export",
                            "--expected-revision",
                            "1",
                            "--stages-file",
                            str(stage_file),
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
                        command("task", "note", "HAR-1", "--body", "Serializer verified.", "--json")
                        command(
                            "task",
                            "handoff",
                            "HAR-1",
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
                    task = json.loads(command("task", "show", "HAR-1", "--json"))
                    assert task["key"] == "HAR-1"
                    assert task["handoff"]["id"] == "installed-handoff"
                    assert task["handoff"]["needs_recheck"] is False
                    activity = json.loads(
                        command("task", "activity", "HAR-1", "--current", "--json")
                    )
                    assert activity["items"][0]["id"] == "installed-handoff"
                    assert json.loads(read("/api/context/projects/harbor/tasks/HAR-1")) == task
                    assert task["revision"] == 3
                    assert task["status"] == "up_next"
                    assert "revisions" not in task
                    assert (
                        len(json.loads(command("task", "revisions", "HAR-1", "--json"))["items"])
                        == 3
                    )
                    board = json.loads(command("status", "--json"))
                    assert board["columns"][1]["tasks"][0]["key"] == task["key"]
                    assert board["recommendation"]["action"] == "publish"
                    workers = json.loads(command("project", "workers", "show", "--json"))
                    assert workers["selection"] is None and workers["max_parallel"] == 1
                    assert workers["enabled"] is False
                    assert json.loads(command("task", "runs", "list", "--json"))["items"] == []
                    assert board["observed_at"]
                    assert b"<html" in read("/projects/harbor/tasks/HAR-1/activity").lower()
                    asyncio.run(check_mcp(base, task))
                    assert original.read_text() == "Preserve existing instructions.\n"
                    assert (state / "workspace.sqlite3").is_file()
                    assert (state / "artifacts").is_dir()
                    updates = json.loads(command("update", "status", "--json"))
                    assert updates["automatic"] is False and updates["last_attempt"] is None
                    notices = json.loads(read("/api/notifications"))
                    saved_notice = next(
                        n for n in notices["items"] if n["title"] == "Package verification"
                    )
                    if attempt == 1:
                        remaining = write(
                            "/api/notifications/dismiss", {"ids": [saved_notice["id"]]}
                        )
                        assert all(n["id"] != saved_notice["id"] for n in remaining["items"])
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
        "task board, briefing/handoff, activity, revisions, restart "
        "offline storage recovery, persistent notifications and update status passed."
    )


if __name__ == "__main__":
    main()
