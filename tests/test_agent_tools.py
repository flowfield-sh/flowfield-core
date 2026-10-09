"""Both roles use the same native client and real scoped MCP transport."""

import asyncio
import json

import httpx
from test_execution import BASE, fixture
from test_native_adapters import start

from flowfield.adapters.agent_mcp import serve_scope
from flowfield.agent_tools import coordinator_scope, worker_scope
from flowfield.mcp import create_mcp
from flowfield.supervisor import Supervisor, WorkerBridge


def test_project_paging_contracts_are_declared_and_enforced(tmp_path):
    execution = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(Supervisor(execution.workspace), "harbor")
        for tool in grant.tools.values():
            properties = tool.inputSchema["properties"]
            if "limit" in properties:
                limit = properties["limit"]
                assert limit["minimum"] == 1, tool.name
                assert limit["maximum"] >= limit["default"], tool.name
        assert (
            grant.tools["get_task_conversation"].inputSchema["properties"]["limit"]["maximum"] == 30
        )
        assert grant.tools["get_results"].inputSchema["properties"]["limit"]["maximum"] == 10
        oversized = await grant.call("list_tasks", {"limit": 100})
        assert oversized.isError
        assert oversized.structuredContent["error"]["code"] == "invalid_request"
        valid = await grant.call("list_tasks", {"limit": 20})
        assert not valid.isError
        assert "items" in valid.structuredContent
        grant.revoke()

    asyncio.run(exercise())


def test_coordinator_reads_overlap_with_bounded_revocable_access(tmp_path):
    service = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(Supervisor(service.workspace), "harbor")
        original = grant._call
        entered = asyncio.Queue()
        release = asyncio.Event()

        async def delayed(name, arguments):
            entered.put_nowait(name)
            await release.wait()
            return await original(name, arguments)

        grant._call = delayed
        calls = []
        for name in ["get_project", "get_board"] * 4:
            calls.append(asyncio.create_task(grant.call(name, {})))
            assert await asyncio.wait_for(entered.get(), 2) == name
        excess = await grant.call("get_board", {})
        assert excess.structuredContent["error"]["code"] == "tool_busy"
        # Cancellation releases capacity; revocation rejects new work without
        # falsely claiming to cancel operations already accepted.
        calls.pop().cancel()
        await asyncio.sleep(0)
        replacement = asyncio.create_task(grant.call("get_board", {}))
        assert await asyncio.wait_for(entered.get(), 2) == "get_board"
        calls.append(replacement)
        grant.revoke()
        closed = await grant.call("get_board", {})
        assert closed.structuredContent["error"]["code"] == "scope_closed"
        release.set()
        results = await asyncio.gather(*calls)
        assert all(not result.isError for result in results)
        assert grant._reading == 0

    asyncio.run(exercise())


async def journey(tmp_path, grant, calls):
    events = []
    async with serve_scope(grant) as server:
        client = await start(tmp_path, events, servers=[server])
        try:
            assert (await client.prompt(json.dumps({"calls": calls}), None))[
                "status"
            ] == "completed"
        finally:
            assert await client.stop()
    assert grant.revoked
    return [
        json.loads(event.text)
        for event in events
        if event.kind == "agent" and event.text.startswith("{")
    ]


def test_coordinator_captures_work_in_fixed_project(tmp_path):
    execution = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(Supervisor(execution.workspace), "harbor")
        canonical = create_mcp(lambda: execution.workspace, lambda: Supervisor(execution.workspace))
        names = {tool.name for tool in await canonical.list_tools()}
        assert set(grant.tools) == names - {"list_projects", "initialize_project"}
        for tool in grant.tools.values():
            assert "project_id" not in tool.inputSchema["properties"]
        events = await journey(
            tmp_path,
            grant,
            [
                {"name": "get_board"},
                {
                    "name": "create_task",
                    "arguments": {
                        "task": {
                            "stages": [
                                {
                                    "id": "report",
                                    "title": "Report",
                                    "outcome": "Report the agreed findings",
                                }
                            ],
                            "id": "captured",
                            "title": "Captured in conversation",
                            "body": "Agreed outcome",
                            "author": "human",
                        }
                    },
                },
                {"name": "get_board", "arguments": {"project_id": "elsewhere"}},
                {"name": "initialize_project", "arguments": {}},
                {"name": "get_task_stages", "arguments": {"task_id": "task-0"}},
            ],
        )
        assert len(events) == 6
        results = [event["result"] for event in events[1:]]
        assert not results[0]["isError"] and not results[1]["isError"], results
        assert results[2]["isError"] and results[3]["isError"]
        assert not results[4]["isError"]
        assert execution.workspace.task("harbor", "captured").title == "Captured in conversation"
        assert execution.workspace.task("harbor", "captured").updated_by == "agent"
        denied = await grant.call("get_board", {})
        assert denied.isError and denied.structuredContent["error"]["code"] == "scope_closed"

    asyncio.run(exercise())


def test_worker_reads_runs_submits_and_closes_same_bridge(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)

    class Commands:
        async def command(self, script, *, timeout_ms):
            # A deterministic executor, deliberately not a sandbox or live Codex proof.
            assert script == "perform assigned check"
            (tmp_path / "executed").write_text("done")
            return {"exitCode": 0, "stdout": "checked", "stderr": ""}

    async def exercise():
        bridge = WorkerBridge(execution, run, Commands())
        events = await journey(
            tmp_path,
            worker_scope(bridge),
            [
                {"name": "read_context", "arguments": {"section": "description"}},
                {"name": "run_command", "arguments": {"command": "perform assigned check"}},
                {
                    "name": "submit_result",
                    "arguments": {
                        "outcome": "complete",
                        "summary": "Checked",
                        "checks": "Deterministic check",
                    },
                },
                {"name": "run_command", "arguments": {"command": "late write"}},
            ],
        )
        results = [event["result"] for event in events[1:]]
        assert not results[0]["isError"] and not results[2]["isError"], results
        assert results[1]["isError"]
        assert results[3]["isError"]
        assert not (tmp_path / "executed").exists()  # Native commands never pass through MCP.
        assert bridge.result.summary == "Checked"
        # Saving a report is not execution finish, code approval or delivery.
        assert execution.get("harbor", run.id).status == "running"

    asyncio.run(exercise())


def test_scoped_endpoint_rejects_ambient_browser_and_expired_access(tmp_path):
    execution = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(Supervisor(execution.workspace), "harbor")
        async with serve_scope(grant) as server:
            headers = {item.name: item.value for item in server.headers}
            async with httpx.AsyncClient() as client:
                assert (await client.get(server.url)).status_code == 403
                assert (
                    await client.get(
                        server.url, headers={**headers, "Origin": "https://evil.example"}
                    )
                ).status_code == 403
                assert (
                    await client.get(server.url, headers={**headers, "Host": "evil.example"})
                ).status_code == 421
                assert (
                    await client.post(server.url, headers=headers, content=b"x" * 65537)
                ).status_code == 413
                grant.revoke()
                assert (await client.get(server.url, headers=headers)).status_code == 403
        async with serve_scope(
            await coordinator_scope(Supervisor(execution.workspace), "harbor"), lifetime=-1
        ) as server:
            async with httpx.AsyncClient(
                headers={item.name: item.value for item in server.headers}
            ) as client:
                assert (await client.get(server.url)).status_code == 403

    asyncio.run(exercise())


def test_pending_workflow_operation_blocks_report_and_revocation_prevents_late_work(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class ControlledBridge(WorkerBridge):
            async def call(self, name, arguments):
                started.set()
                await release.wait()
                return await super().call(name, arguments)

        grant = worker_scope(ControlledBridge(execution, run, None))
        command = asyncio.create_task(grant.call("read_context", {"section": "description"}))
        await asyncio.wait_for(started.wait(), 2)
        result = await grant.call(
            "submit_result", {"outcome": "complete", "summary": "Late", "checks": "none"}
        )
        assert result.isError and result.structuredContent["error"]["code"] == "tool_busy"
        grant.revoke()
        release.set()
        await command
        assert (await grant.call("submit_result", {})).isError

    asyncio.run(exercise())


def test_coordinator_configures_and_validates_only_its_project(tmp_path, monkeypatch):
    from test_setup_validation import configured

    _, validation, settings = configured(tmp_path, monkeypatch)
    service = Supervisor(validation.workspace)

    async def exercise():
        grant = await coordinator_scope(service, "project")
        read = await grant.call("get_integration_settings", {})
        assert not read.isError
        configured_result = await grant.call(
            "configure_integration",
            {
                "settings": {
                    "expected_revision": settings.revision,
                    "target_branch": "delivery",
                    "checks": ["test -f base.txt"],
                    "setup_commands": ['test -n "$FLOWFIELD_RUNTIME_DIR"'],
                }
            },
        )
        assert not configured_result.isError, configured_result
        actual = service.integrations.settings("project")
        checked = await grant.call(
            "validate_project_setup", {"request": {"expected_revision": actual.revision}}
        )
        assert not checked.isError, checked
        assert service.setup_validation.get("project").status == "passed"
        assert not service.execution.settings("project").enabled
        assert not (await grant.call("get_inspection_settings", {})).isError
        assert (
            await grant.call("configure_integration", {"project_id": "other", "settings": {}})
        ).isError
        assert {
            "set_queue",
            "review_result",
            "configure_workers",
            "list_worker_models",
        } <= grant.tools.keys()

    asyncio.run(exercise())


def test_new_project_tools_are_available_without_changing_coordinator_scope(tmp_path, monkeypatch):
    import flowfield.agent_tools as tools

    execution = fixture(tmp_path)

    def registry(*args, **kwargs):
        canonical = create_mcp(*args, **kwargs)

        @canonical.tool()
        def future_project_operation(project_id: str, name: str) -> dict:
            return {"project": project_id, "name": name}

        return canonical

    monkeypatch.setattr(tools, "create_mcp", registry)

    async def exercise():
        grant = await coordinator_scope(Supervisor(execution.workspace), "harbor")
        result = await grant.call("future_project_operation", {"name": "Available immediately"})
        assert not result.isError
        assert json.loads(result.content[0].text) == {
            "project": "harbor",
            "name": "Available immediately",
        }
        assert (
            await grant.call("future_project_operation", {"project_id": "other", "name": "x"})
        ).isError
        assert "list_projects" not in grant.tools and "initialize_project" not in grant.tools
        grant.revoke()
        assert (await grant.call("future_project_operation", {"name": "late"})).isError

    asyncio.run(exercise())


def test_coordinator_reviews_exact_result_and_service_delivers_with_queue_paused(tmp_path):
    from test_results import current
    from test_results import fixture as result_fixture

    from flowfield.actors import actor

    service, repo, _ = result_fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    assert version.status == "ready"

    async def exercise():
        grant = await coordinator_scope(service, "harbor", author="coordinator:saved-turn")
        assert not (await grant.call("get_result", {"result_id": version.id})).isError
        assert not (await grant.call("get_task_input", {"task_id": "work"})).isError
        request = {
            "result_id": version.id,
            "review": {
                "expected_revision": version.revision,
                "candidate_commit": "wrong",
                "action": "approve",
            },
        }
        wrong = await grant.call("review_result", request)
        assert wrong.isError
        assert current(service).approved_at is None
        request["review"]["candidate_commit"] = version.candidate_commit
        request["review"]["expected_revision"] += 1
        assert (await grant.call("review_result", request)).isError
        request["review"]["expected_revision"] = version.revision
        # Omitting author cannot inherit the request model's human default.
        approved = await grant.call("review_result", request)
        assert not approved.isError, approved
        assert current(service).approved_by == "coordinator:saved-turn"
        assert actor(current(service).approved_by).label == "Coordinator"
        assert not service.execution.settings("harbor").enabled
        grant.revoke()

    asyncio.run(exercise())
    service.results.process("harbor")
    assert current(service).status == "delivered"
    assert service.workspace.task("harbor", "work").status == "done"
    from flowfield.adapters.git_workspace import git

    assert git(repo, "rev-parse", "HEAD").decode().strip() == version.candidate_commit


def test_coordinator_controls_workers_and_preserves_worker_scope(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)

    async def validate(self, choice, project_id=None):
        assert choice.model == "fixture" and choice.effort == "low"

    monkeypatch.setattr(Supervisor, "validate_agent_choice", validate)

    async def exercise():
        grant = await coordinator_scope(service, "harbor")
        settings = execution.settings("harbor")
        saved = await grant.call(
            "configure_workers",
            {
                "settings": {
                    "expected_revision": settings.revision,
                    "selection": {"model": "fixture", "effort": "low"},
                    "max_parallel": 2,
                }
            },
        )
        assert not saved.isError, saved
        settings = execution.settings("harbor")
        assert settings.max_parallel == 2
        assert not (
            await grant.call(
                "set_queue", {"change": {"expected_revision": settings.revision, "enabled": False}}
            )
        ).isError
        assert not execution.settings("harbor").enabled
        run = execution.page("harbor").items
        assert not run
        settings = execution.settings("harbor")
        assert not (
            await grant.call(
                "set_queue", {"change": {"expected_revision": settings.revision, "enabled": True}}
            )
        ).isError
        run = execution.claim("harbor", BASE, {BASE: set()})
        assert run
        execution.started("harbor", run.id)
        worker = worker_scope(WorkerBridge(execution, run, None))
        assert set(worker.tools) == {
            "read_context",
            "search_context",
            "update_stages",
            "ask_question",
            "submit_result",
        }
        for name in set(grant.tools) - set(worker.tools):
            denied = await worker.call(name, {})
            assert denied.structuredContent["error"]["code"] == "operation_denied"
        stopped = await grant.call(
            "stop_run", {"run_id": run.id, "request": {"expected_revision": run.revision}}
        )
        assert not stopped.isError, stopped
        assert execution.get("harbor", run.id).status == "stopped"
        retried = await grant.call(
            "retry_run",
            {
                "run_id": run.id,
                "request": {"expected_revision": execution.get("harbor", run.id).revision},
            },
        )
        assert not retried.isError, retried
        assert execution.workspace.task("harbor", run.task_id).status == "up_next"

    asyncio.run(exercise())


def test_coordinator_relays_bound_answer_once_without_enabling_queue(tmp_path):
    from test_input_continuation import question, queue

    from flowfield.questions import Questions
    from flowfield.reply_models import ReplyBinding

    execution = fixture(tmp_path)
    run, question_record = question(execution)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    queue(execution, False)

    async def exercise():
        grant = await coordinator_scope(
            Supervisor(execution.workspace), "harbor", author="coordinator:answer-turn"
        )
        gate = await grant.call("get_task_input", {"task_id": run.task_id})
        binding = {key: gate.structuredContent[key] for key in ReplyBinding.model_fields}
        request = {
            "id": "answer-once",
            "binding": binding,
            "body": "All filtered rows",
            "action": "answer",
            "author": "human",
        }
        saved = await grant.call("reply_to_task", {"task_id": run.task_id, "request": request})
        assert not saved.isError, saved
        repeated = await grant.call("reply_to_task", {"task_id": run.task_id, "request": request})
        assert not repeated.isError
        assert repeated.structuredContent == saved.structuredContent
        answer = Questions(execution.workspace).get("harbor", question_record.id)
        assert answer.answer == "All filtered rows"
        assert answer.updated_by == "coordinator:answer-turn"
        assert not execution.settings("harbor").enabled
        assert execution.claim("harbor", BASE, {BASE: set()}) is None

    asyncio.run(exercise())
    queue(execution, True)
    successor = execution.claim("harbor", BASE, {BASE: set()})
    assert successor and successor.predecessor_id == run.id
    assert "All filtered rows" in execution.assignment("harbor", successor.id)["input"]
    assert execution.claim("harbor", BASE, {BASE: set()}) is None
