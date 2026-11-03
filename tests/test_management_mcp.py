"""GUI/Coordinator configuration parity, scope and exact revision failures."""

import asyncio
import base64

import httpx
import pytest
from execution_fixtures import BASE, fixture
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from permissions_fixtures import OPTIONS, pending
from service_fixtures import running_service

from flowfield.agent_tools import worker_scope, workspace_scope
from flowfield.application import Workspace
from flowfield.document_settings import DocumentSettingsService
from flowfield.documents import DocumentCreate, Documents
from flowfield.supervisor import Supervisor, WorkerBridge


def test_service_registry_reuse_keeps_workspace_authorship_and_revocation_separate(tmp_path):
    from flowfield.mcp import create_mcp
    from flowfield.workspaces import WorkspaceCreate, Workspaces

    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)
    from workspace_fixtures import TEST_AGENTS

    other = Workspaces(execution.workspace).create(
        WorkspaceCreate(name="Projectless", **TEST_AGENTS)
    )

    async def exercise():
        first = await workspace_scope(service, "harbor", author="coordinator:first")
        second = await workspace_scope(service, other.id, author="coordinator:second")
        public = create_mcp(lambda: execution.workspace, lambda: service, origin="")
        assert (
            "workspace_id"
            in (await service.coordinator_tools.list_tools())[0].inputSchema["properties"]
        )
        assert "workspace_id" not in second.tools["create_task"].inputSchema["properties"]
        first.revoke()
        blocked = await first.call("create_task", {"task": {"title": "Blocked"}})
        assert blocked.structuredContent["error"]["code"] == "scope_closed"
        created = await second.call("create_task", {"task": {"title": "Unique search evidence"}})
        assert not created.isError
        assert execution.workspace.task(other.id, "T-1").updated_by == "coordinator:second"
        found = await second.call("search_context", {"query": "Unique"})
        assert (
            not found.isError
            and found.structuredContent["items"][0]["identity"] == created.structuredContent["id"]
        )
        external = await public.call_tool(
            "search_context", {"workspace_id": other.id, "query": "Unique"}
        )
        assert external.structuredContent == found.structuredContent
        empty = await public.call_tool(
            "search_context", {"workspace_id": "harbor", "query": "Unique"}
        )
        assert empty.structuredContent["items"] == []
        second.revoke()

    asyncio.run(exercise())


def test_mcp_failed_setup_retains_paged_log_across_retry(tmp_path, monkeypatch):
    from setup_validation_fixtures import configured

    from flowfield.mcp import create_mcp

    _, setup, settings = configured(
        tmp_path, monkeypatch, setup_commands=["printf 'first line\\nsecond line\\n'; exit 1"]
    )
    service = Supervisor(setup.workspace)
    server = create_mcp(lambda: setup.workspace, lambda: service, origin="")

    async def exercise():
        async def call(name, **args):
            result = await server.call_tool(name, {"project_id": "project", **args})
            assert not result.isError, result
            return result.structuredContent

        failed = await call(
            "validate_project_setup", request={"expected_revision": settings.revision}
        )
        assert failed["status"] == "failed" and not failed["checks"]
        args = {"check_id": failed["id"], "phase": "setup", "index": 0, "limit": 7}
        page = await call("get_setup_log", **args)
        text = page["text"]
        digest = page["sha256"]
        while page["next_request"]:
            args.update(page["next_request"])
            page = await call("get_setup_log", **args)
            assert page["sha256"] == digest
            text += page["text"]
        assert "first line\nsecond line\n" in text
        retried = await call(
            "validate_project_setup", request={"expected_revision": settings.revision}
        )
        assert retried["id"] != failed["id"]
        retained = await call("get_setup_log", **{**args, "offset": 0, "limit": 6000})
        assert retained["text"] == text
        assert not service.execution.settings("project").enabled

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario", ["wait", "cleanup-uncertain", "resume-missing"])
def test_external_coordinator_control_and_exact_recovery_use_mcp(tmp_path, monkeypatch, scenario):
    from coordinator_fixtures import message, settled, setup

    from flowfield.mcp import create_mcp

    service, _ = setup(
        tmp_path,
        monkeypatch,
        scenario="wait" if scenario == "wait" else "normal",
        flags=() if scenario == "wait" else (scenario,),
    )
    server = create_mcp(lambda: service.workspace, lambda: service, origin="")

    async def call(name, **args):
        result = await server.call_tool(name, {"workspace_id": "harbor", **args})
        assert not result.isError, result
        return result.structuredContent

    async def exercise():
        try:
            sent = await call("send_coordinator_message", request=message().model_dump())
            turn = service.coordinator.store.get("harbor", sent["id"])
            if scenario == "wait":
                stopped = await call("stop_coordinator", turn_id=turn.id)
                assert stopped["status"] == "stopped"
            elif scenario == "cleanup-uncertain":
                assert (await settled(service, turn)).status == "uncertain"
                denied = await server.call_tool(
                    "confirm_coordinator_stopped",
                    {
                        "workspace_id": "harbor",
                        "turn_id": "wrong",
                    },
                )
                assert denied.isError
                recovered = await call("confirm_coordinator_stopped", turn_id=turn.id)
                assert recovered["status"] == "interrupted"
            else:
                assert (await settled(service, turn)).status == "completed"
                sent = await call(
                    "send_coordinator_message", request=message("Continue").model_dump()
                )
                failed = await settled(service, service.coordinator.store.get("harbor", sent["id"]))
                assert failed.status == "failed"
                reset = await call("reset_coordinator_session", turn_id=failed.id)
                assert reset["saved"]
            assert service.coordinator.store.page("harbor").items
        finally:
            await service.close()

    asyncio.run(exercise())


def test_external_mcp_creates_projectless_work_and_matches_browser_settings(tmp_path):
    async def exercise(base):
        async with (
            streamable_http_client(base + "/mcp/") as (read, write, _),
            ClientSession(read, write) as session,
            httpx.AsyncClient(base_url=base, trust_env=False) as api,
        ):
            await session.initialize()

            async def call(name, **args):
                result = await session.call_tool(name, args)
                assert not result.isError, result
                return result.structuredContent

            first = await call(
                "create_workspace",
                request={
                    "name": "First",
                    **{
                        role: {"harness": "codex", "model": "test-model"}
                        for role in ("coordinator", "worker", "reviewer")
                    },
                },
            )
            second = await call(
                "create_workspace",
                request={
                    "name": "Second",
                    **{
                        role: {"harness": "codex", "model": "test-model"}
                        for role in ("coordinator", "worker", "reviewer")
                    },
                },
            )
            scope = {"workspace_id": first["id"]}
            body = "Retained source " * 1000
            task = await call(
                "create_task", **scope, task={"title": "Launch research", "body": body}
            )
            assert task["key"] == "T-1"
            assert (await call("list_projects"))["items"] == []
            shown = await call("get_task", **scope, task_id="t-1")
            assert shown["truncated_fields"]["body"]["total_characters"] == len(body)
            assert (
                await call(
                    "get_text", **scope, resource="task", identity="T-1", field="body", revision=1
                )
            )["text"] == body[:4000]
            await call(
                "edit_task",
                **scope,
                task_id="T-1",
                changes={"expected_revision": 1, "title": "Revised launch"},
            )
            assert (
                await call(
                    "get_text", **scope, resource="task", identity="T-1", field="body", revision=1
                )
            )["text"] == body[:4000]
            stale = await session.call_tool(
                "edit_task",
                {**scope, "task_id": "T-1", "changes": {"expected_revision": 1, "title": "Lost"}},
            )
            assert stale.isError and stale.structuredContent["error"]["code"] == "revision_conflict"
            foreign = await session.call_tool(
                "get_task", {"workspace_id": second["id"], "task_id": "T-1"}
            )
            assert foreign.isError
            child = await call(
                "create_subtask",
                **scope,
                task_id="T-1",
                subtask={
                    "title": "Collect facts",
                    "body": "Research the launch",
                    "completion": "report",
                },
            )
            assert child["key"] == "S-1"
            for tool, path in (
                ("get_coordinator_settings", "coordinator-settings"),
                ("get_reviewer_settings", "reviewer"),
                ("get_worker_occupancy", "workers/occupancy"),
            ):
                assert (
                    await call(tool, **scope)
                    == (await api.get(f"/api/workspaces/{first['id']}/{path}")).json()
                )
            saved = await call("configure_notifications", request={"work_enabled": False})
            assert saved == (await api.get("/api/announcements/settings")).json()
            assert not saved["work_enabled"] and saved["updates_enabled"]
            changed = await call("configure_updates", automatic=False)
            assert changed == (await api.get("/api/updates")).json()
            assert not changed["automatic"] and not changed["checking"]
            registration = await call(
                "configure_harness",
                harness="pi",
                request={"expected_revision": 1, "executable": str(tmp_path / "native-pi")},
            )
            assert registration["revision"] == 2
            read = await call("get_harness_settings", harness="pi")
            assert read == (await api.get("/api/harnesses/pi")).json()
            bad = await session.call_tool(
                "configure_harness", {"harness": "pi", "request": {"expected_revision": 1}}
            )
            assert bad.isError and bad.structuredContent["error"]["code"] == "revision_conflict"
            grant = await workspace_scope(Supervisor(Workspace(tmp_path / "state")), first["id"])
            for args in (
                {"harness": "pi", "request": {"expected_revision": 1}},
                {"harness": "pi", "request": {"expected_revision": "invalid"}},
            ):
                public = await session.call_tool("configure_harness", args)
                scoped = await grant.call("configure_harness", args)
                assert public.isError and scoped.isError
                assert public.structuredContent == scoped.structuredContent
            recovered = await call(
                "configure_harness", harness="pi", request={"expected_revision": 2}
            )
            assert recovered["revision"] == 3
            grant.revoke()

    with running_service(tmp_path / "state") as base:
        asyncio.run(exercise(base))


def test_scoped_configuration_keeps_attribution_atomicity_and_role_boundaries(
    tmp_path, monkeypatch
):
    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)
    choices = []

    async def validate(choice, project_id=None, **kwargs):
        choices.append((choice, project_id, kwargs))

    monkeypatch.setattr(service, "validate_agent_choice", validate)

    async def exercise():
        grant = await workspace_scope(service, "harbor", author="coordinator:settings-turn")

        async def call(name, **args):
            result = await grant.call(name, args)
            assert not result.isError, result
            return result.structuredContent

        assert "send_coordinator_message" not in grant.tools
        assert "create_workspace" not in grant.tools
        saved = await call(
            "configure_task_worker",
            task_id="T-1",
            request={
                "expected_revision": 1,
                "selection": {"harness": "pi", "model": "fixture-provider/model"},
            },
        )
        assert saved["effective"]["source"] == "override"
        assert choices[-1][1] == "harbor"
        inherited = await call(
            "configure_task_worker",
            task_id="T-1",
            request={"expected_revision": saved["revision"], "selection": None},
        )
        assert inherited["effective"]["source"] == "workspace"
        coordinator = await call("get_coordinator_settings")
        saved_coordinator = await call(
            "configure_coordinator",
            request={
                "expected_revision": coordinator["revision"],
                "selection": {"harness": "codex", "model": "changed"},
            },
        )
        assert saved_coordinator["selection"]["model"] == "changed"
        reviewer = await call("get_reviewer_settings")
        reviewer = await call(
            "configure_reviewer_settings",
            request={"expected_revision": reviewer["revision"], "enabled": False},
        )
        assert not reviewer["effective"]["enabled"]
        task_reviewer = await call(
            "configure_reviewer_settings",
            task_id="T-1",
            request={
                "expected_revision": 1,
                "enabled": True,
                "selection": {"harness": "codex", "model": "review-fixture"},
            },
        )
        assert task_reviewer["effective"]["enabled"]
        assert choices[-1][1] is None and "project_path" in choices[-1][2]
        denied = await grant.call(
            "configure_task_worker",
            {"workspace_id": "elsewhere", "task_id": "T-1", "request": {"expected_revision": 1}},
        )
        assert denied.isError and denied.structuredContent["error"]["code"] == "scope_mismatch"
        denied = await grant.call(
            "configure_check_permission",
            {
                "project_id": "elsewhere",
                "request": {"expected_revision": 1, "settings_revision": 1, "enabled": True},
            },
        )
        assert denied.isError
        doc = Documents(execution.workspace).create(
            "harbor", DocumentCreate(title="Plan", body="original")
        )
        doc_settings = await call("get_document_settings", document_id=doc.id, limit=1)
        assert (
            doc_settings["body"] == "original"
            and doc_settings["usage"]["projects"][0]["id"] == "harbor"
        )
        saved = await call(
            "edit_document_settings",
            document_id=doc.id,
            request={
                "expected_revision": 1,
                "body": "revised",
                "author": "human",
                "usage": {"expected_revision": 1, "projectless": "required", "author": "human"},
            },
        )
        current = DocumentSettingsService(execution.workspace).get("harbor", doc.id)
        assert current.body == "revised" and current.author == "coordinator:settings-turn"
        conflict = await grant.call(
            "edit_document_settings",
            {
                "document_id": doc.id,
                "request": {
                    "expected_revision": saved["revision"],
                    "body": "lost",
                    "usage": {"expected_revision": 1, "projectless": "reference"},
                },
            },
        )
        assert conflict.isError
        assert DocumentSettingsService(execution.workspace).get("harbor", doc.id) == current
        attachment = await call(
            "upload_attachment",
            request={
                "name": "notes.txt",
                "mime": "text/plain",
                "data": base64.b64encode(b"User supplied notes").decode(),
            },
        )
        assert attachment["href"].startswith("/api/workspaces/harbor/attachments/")
        run = execution.claim("harbor", BASE, {BASE: set()})
        worker = worker_scope(WorkerBridge(execution, run, None))
        assert {
            "configure_task_worker",
            "configure_reviewer_settings",
            "answer_permission",
            "configure_harness",
            "configure_updates",
            "get_execution_log",
        }.isdisjoint(worker.tools)
        worker.revoke()
        grant.revoke()

    asyncio.run(exercise())


def test_task_worker_settings_reject_stale_before_discovery_and_recheck_after_it(
    tmp_path, monkeypatch
):
    from flowfield.agent_configuration import configure_worker
    from flowfield.agent_models import AgentChoice, AgentSettingsEdit
    from flowfield.agent_settings import AgentSettings
    from flowfield.errors import ApplicationError

    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)
    owner = AgentSettings(execution.workspace)
    request = AgentSettingsEdit(expected_revision=1, selection=AgentChoice(model="new"))
    owner.edit("harbor", "worker", request, "T-1")
    calls = []

    async def validate(*args, **kwargs):
        calls.append(args)
        owner.edit("harbor", "worker", request.model_copy(update={"expected_revision": 2}), "T-1")

    monkeypatch.setattr(service, "validate_agent_choice", validate)

    async def exercise():
        with pytest.raises(ApplicationError) as stale:
            await configure_worker(service, "harbor", "T-1", request)
        assert stale.value.code == "revision_conflict" and not calls
        with pytest.raises(ApplicationError) as raced:
            await configure_worker(
                service, "harbor", "T-1", request.model_copy(update={"expected_revision": 2})
            )
        assert raced.value.code == "revision_conflict" and len(calls) == 1
        assert owner.get("harbor", "worker", "T-1").revision == 3

    asyncio.run(exercise())


def test_scoped_permission_relay_keeps_exact_live_binding(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    service = Supervisor(execution.workspace)

    async def exercise():
        grant = await workspace_scope(service, "harbor")
        async with service.permissions.turn(
            "harbor", "worker", run_id=run.id, session_id="session", turn_id="turn"
        ) as turn:
            options = [
                OPTIONS[0].model_copy(update={"label": "Allow this exact command " * 100}),
                OPTIONS[1],
            ]
            waiting = asyncio.create_task(turn.request("read", "Read file", options))
            record = await pending(service.permissions)
            page = await grant.call("get_permission", {"permission_id": record.id})
            assert page.structuredContent["id"] == record.id
            first = await grant.call(
                "get_permission", {"permission_id": record.id, "option_id": "yes", "limit": 50}
            )
            assert first.structuredContent["options"][0]["truncated_fields"]["label"]
            next_page = await grant.call(
                "get_permission",
                {"permission_id": record.id, **first.structuredContent["next_request"]},
            )
            assert (
                not next_page.isError
                and next_page.structuredContent["text"] == options[0].label[50:100]
            )
            stale = await grant.call(
                "answer_permission",
                {
                    "permission_id": record.id,
                    "request": {"expected_revision": 99, "option_id": OPTIONS[0].id},
                },
            )
            assert stale.isError and not waiting.done()
            answered = await grant.call(
                "answer_permission",
                {
                    "permission_id": record.id,
                    "request": {
                        "expected_revision": record.revision,
                        "option_id": record.options[0].id,
                    },
                },
            )
            assert not answered.isError
            assert await waiting
        again = await grant.call(
            "answer_permission",
            {
                "permission_id": record.id,
                "request": {
                    "expected_revision": record.revision,
                    "option_id": record.options[1].id,
                },
            },
        )
        assert again.isError
        grant.revoke()

    asyncio.run(exercise())


def test_scoped_evidence_reads_exact_logs_diffs_and_review_recovery(tmp_path):
    from task_reviews_fixtures import configured

    from flowfield.check_logs import read as read_log
    from flowfield.execution_evidence import command_receipts

    service, _, run, _, reviews = configured(tmp_path)

    async def exercise():
        review = reviews.prepare("harbor", service.results.page("harbor", run.task_id).items[0].id)
        grant = await workspace_scope(service, "harbor")

        async def call(name, **args):
            result = await grant.call(name, args)
            assert not result.isError, result
            return result.structuredContent

        missing = await grant.call("get_execution_diff", {"source": "run", "identity": run.id})
        assert missing.isError and missing.structuredContent["error"]["code"] == "result_missing"
        result = service.results.page("harbor", run.task_id).items[0]
        diff = await call(
            "get_execution_diff", source="integration", identity=result.integration_id, limit=1
        )
        assert diff["files"]
        patch = await call(
            "get_execution_patch", source="integration", identity=result.integration_id, file_id=0
        )
        assert patch["file"]["new_path"] == "result.txt"
        assert "+implemented" in patch["text"] and not patch["omitted_reason"]
        foreign = await grant.call(
            "get_execution_log",
            {
                "workspace_id": "other",
                "source": "run",
                "identity": run.id,
                "phase": "checks",
                "index": 0,
            },
        )
        assert foreign.isError
        result = service.results.page("harbor", run.task_id).items[0]
        runtime, checks = command_receipts(
            service, "harbor", "integration", result.integration_id, "checks"
        )
        assert checks and checks[0].log_id
        full = read_log(runtime, checks, checks[0].log_id)
        log = await call(
            "get_execution_log",
            source="integration",
            identity=result.integration_id,
            phase="checks",
            index=0,
            limit=1,
        )
        assert log["text"] == full[:1] and log["total_chars"] == len(full)
        (runtime / "logs" / checks[0].log_id).write_text("changed")
        changed = await grant.call(
            "get_execution_log",
            {
                "source": "integration",
                "identity": result.integration_id,
                "phase": "checks",
                "index": 0,
            },
        )
        assert changed.isError
        review = reviews.claim("harbor", review.id, review.revision)
        review = reviews.begin_attempt("harbor", review.id)
        review = reviews.started("harbor", review.id, review.revision)
        stopped = reviews.finish_attempt(
            "harbor",
            review.id,
            review.attempt_id,
            cleanup_confirmed=True,
            problem="Scripted reviewer failed",
        )
        state = await call("get_review", review_id=review.id)
        assert state["status"] == "failed" and "binding" not in state
        attempts = await call("get_review_attempts", review_id=review.id)
        assert len(attempts["items"]) == 1
        assert attempts["items"][0]["id"] == review.attempt_id
        assert attempts["items"][0]["status"] == "failed"
        retried = await call(
            "retry_review", review_id=review.id, request={"expected_revision": stopped.revision}
        )
        assert retried["status"] == "queued"
        retried_review = reviews.claim("harbor", review.id, retried["revision"])
        second = reviews.begin_attempt("harbor", retried_review.id)
        history = (await call("get_review_attempts", review_id=review.id))["items"]
        assert [item["id"] for item in history] == [second.attempt_id, review.attempt_id]
        assert history[1]["status"] == "failed"
        reviews.finish_attempt(
            "harbor",
            review.id,
            second.attempt_id,
            cleanup_confirmed=True,
            problem="Stopped fixture",
        )
        stale = await grant.call(
            "retry_review",
            {"review_id": review.id, "request": {"expected_revision": stopped.revision}},
        )
        assert stale.isError
        grant.revoke()

    asyncio.run(exercise())
