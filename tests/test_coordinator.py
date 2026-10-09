"""Durable browser coordination using real native subprocesses and scoped MCP, without models."""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from project_fixtures import task_request
from test_execution import BASE, fixture
from test_managed_local import FAKE, configured

from flowfield import migrations
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.api import create_app
from flowfield.application import Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.permission_models import PermissionAnswer
from flowfield.run_activity import MAX_TOTAL, ActivityUpdate, ContextUsage
from flowfield.supervisor import Supervisor


def setup(tmp_path, monkeypatch, scenario="normal", flags=()):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario=scenario)
    monkeypatch.setattr(
        "flowfield.adapters.codex_agent.command",
        lambda directory, env: (
            [
                sys.executable,
                str(FAKE),
                "managed",
                "coordinator",
                "cleanup",
                "close-session",
                *flags,
            ],
            dict(env),
        ),
    )
    AgentSettings(service.workspace).edit(
        "harbor",
        "coordinator",
        AgentSettingsEdit(
            expected_revision=1, selection=AgentChoice(model="test-model", effort="low")
        ),
    )
    conversation = service.coordinator.store.new("harbor")
    return service, conversation


def message(text="Capture the agreed task"):
    return CoordinatorSend(id=uuid4().hex, text=text)


def test_context_report_survives_restart_and_reportless_turns_outside_page(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    store = service.coordinator.store
    usage = ContextUsage(used=12000, size=100000)
    first, _ = store.reserve("harbor", conversation.id, message())
    store.write(
        "harbor", first.id, [ActivityUpdate(key="usage", kind="status", text="", context=usage)]
    )
    with service.workspace.connection(write=True) as db:
        first = store._get(db, "harbor", first.id)
        first.status = "completed"
        first.session = "new"
        store._save(db, first)
    for _ in range(22):
        turn, _ = store.reserve("harbor", conversation.id, message("/status"))
        with service.workspace.connection(write=True) as db:
            turn.status = "completed"
            turn.session = "resumed"
            store._save(db, turn)
    restored = CoordinatorStore(Workspace(service.workspace.directory))
    page = restored.page("harbor")
    assert len(page.items) == 20 and all(item.activity.context is None for item in page.items)
    assert page.context == usage
    assert restored.page("harbor", after=turn.number).context == usage
    active, _ = restored.reserve("harbor", conversation.id, message())
    assert restored.page("harbor").context == usage
    restored.restart()
    assert restored.page("harbor").context == usage
    with service.workspace.connection(write=True) as db:
        active = restored._get(db, "harbor", active.id)
        active.session = "new"
        restored._save(db, active)
    assert restored.page("harbor").context is None


async def settled(service, turn):
    job = service.coordinator.jobs.get(turn.id)
    if job:
        await asyncio.wait_for(asyncio.shield(job), 15)
    return service.coordinator.store.get("harbor", turn.id)


def test_real_native_capture_continuity_and_duplicate_send(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    request = message()

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, request)
        duplicate = service.coordinator.send("harbor", conversation.id, request)
        assert duplicate.id == turn.id and len(service.coordinator.jobs) == 1
        with pytest.raises(ApplicationError, match="active turn"):
            service.coordinator.send("harbor", conversation.id, message())
        completed = await settled(service, turn)
        assert completed.status == "completed", completed.notice
        assert completed.session == "new"
        assert next(item for item in completed.activity.items if item.key == "session").text == (
            "New agent session started."
        )
        native = service.coordinator.store.session("harbor", "codex", str(tmp_path / "harbor"))
        assert native == "test-session"
        with pytest.raises(ApplicationError, match="different harness or project directory"):
            service.coordinator.store.session("harbor", "codex", str(tmp_path / "elsewhere"))
        assert completed.applied.choice.mode == "read-only"
        assert completed.settings.choice.mode == "read-only"
        assert service.workspace.task("harbor", "chat-task").updated_by == f"coordinator:{turn.id}"
        assert not (tmp_path / "harbor" / "result.txt").exists()
        public = completed.model_dump_json()
        assert "finished" in public and "PRIVATE" not in public and "WRONG SESSION" not in public
        assert "do-not-persist-host-secrets" not in public
        assert "test-session" not in public
        second = service.coordinator.send("harbor", conversation.id, message("Check continuity"))
        resumed = await settled(service, second)
        assert resumed.status == "completed"
        assert resumed.session == "resumed"
        assert all(item.key != "session" for item in resumed.activity.items)
        assert service.coordinator.send("harbor", conversation.id, request).id == turn.id
        assert not service.coordinator.jobs
        await service.close()

    asyncio.run(exercise())
    restored = CoordinatorStore(Workspace(service.workspace.directory))
    page = restored.page("harbor", conversation.id)
    assert len(page.items) == 2 and page.active is None
    new = restored.new("harbor")
    assert new.id == conversation.id
    assert restored.page("harbor").items == page.items


def test_native_session_survives_restart_and_interruption_without_replay(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)

    async def exercise():
        first = service.coordinator.send("harbor", conversation.id, message())
        assert (await settled(service, first)).session == "new"
        await service.close()
        restored = Supervisor(Workspace(service.workspace.directory))
        pending, _ = restored.coordinator.store.reserve("harbor", conversation.id, message())
        with restored.workspace.connection(write=True) as db:
            pending.native_started = True
            restored.coordinator.store._save(db, pending)
        restored.coordinator.store.restart()
        assert restored.coordinator.store.get("harbor", pending.id).status == "uncertain"
        with pytest.raises(ApplicationError, match="active turn"):
            restored.coordinator.send("harbor", conversation.id, message())
        assert not restored.coordinator.jobs
        restored.coordinator.store.confirm_stopped("harbor", pending.id)
        next_turn = restored.coordinator.send(
            "harbor", conversation.id, message("Check continuity")
        )
        completed = await settled(restored, next_turn)
        assert completed.status == "completed" and completed.session == "resumed"
        assert restored.coordinator.store.get("harbor", pending.id).status == "interrupted"
        await restored.close()

    asyncio.run(exercise())


def test_missing_native_session_requires_explicit_bound_reset(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch, flags=("resume-missing",))

    async def exercise():
        initial = service.coordinator.send("harbor", conversation.id, message())
        assert (await settled(service, initial)).session == "new"
        retry = service.coordinator.send("harbor", conversation.id, message())
        failed = await settled(service, retry)
        assert failed.status == "failed" and failed.session == "unavailable"
        assert "Nothing was replayed" in failed.notice
        assert service.coordinator.store.page("harbor").session_recovery_turn_id == failed.id
        with pytest.raises(ApplicationError, match="recovery changed"):
            service.coordinator.store.reset_session("harbor", initial.id)
        pending = service.coordinator.send("harbor", conversation.id, message("Retry resume"))
        with pytest.raises(ApplicationError, match="recovery changed"):
            service.coordinator.store.reset_session("harbor", failed.id)
        failed = await settled(service, pending)
        assert failed.session == "unavailable"
        service.coordinator.store.reset_session("harbor", failed.id)
        assert service.coordinator.store.page("harbor").session_recovery_turn_id is None
        with pytest.raises(ApplicationError, match="recovery changed"):
            service.coordinator.store.reset_session("harbor", failed.id)
        fresh = service.coordinator.send("harbor", conversation.id, message())
        done = await settled(service, fresh)
        assert done.status == "completed" and done.session == "new"
        assert "recent saved exchanges" in done.activity.items[0].text
        await service.close()

    asyncio.run(exercise())


def test_saved_history_bootstraps_native_session_once(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    old, _ = service.coordinator.store.reserve(
        "harbor", conversation.id, message("Keep existing intent")
    )
    with service.workspace.connection(write=True) as db:
        old.status = "completed"
        service.coordinator.store._save(db, old)
    restored = Supervisor(Workspace(service.workspace.directory))
    assert restored.workspace.schema_version == migrations.current_version()
    assert restored.coordinator.store.get("harbor", old.id) == old
    assert restored.coordinator.store.session("harbor", "codex", str(tmp_path / "harbor")) is None

    async def exercise():
        first = restored.coordinator.send("harbor", conversation.id, message())
        assert (await settled(restored, first)).session == "new"
        second = restored.coordinator.send("harbor", conversation.id, message("Check continuity"))
        assert (await settled(restored, second)).session == "resumed"
        await restored.close()

    asyncio.run(exercise())


def test_planning_before_worker_delivery_configuration(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    with service.workspace.connection(write=True) as db:
        db.execute("DELETE FROM integration_settings WHERE project_id='harbor'")
    settings = service.integrations.settings("harbor")
    assert settings.checks == []

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, message())
        assert (await settled(service, turn)).status == "completed"
        assert service.workspace.task("harbor", "chat-task").updated_by == f"coordinator:{turn.id}"
        await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("choice", ["allow", "deny"])
def test_native_permission_reaches_coordinator_and_preserves_offered_choices(
    tmp_path, monkeypatch, choice
):
    service, conversation = setup(
        tmp_path, monkeypatch, scenario="permission", flags=("long-permission",)
    )

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, message())
        try:
            async with asyncio.timeout(10):
                while not service.permissions.page("harbor").pending:
                    await asyncio.sleep(0.02)
            pending = service.permissions.page("harbor").pending[0]
            assert pending.role == "coordinator" and pending.turn_id == turn.id
            assert [o.id for o in pending.options] == ["allow", "deny"]
            assert "command: inspect project" in pending.details
            service.permissions.answer(
                "harbor",
                pending.id,
                PermissionAnswer(expected_revision=pending.revision, option_id=choice),
            )
            completed = await settled(service, turn)
            assert completed.status == "completed", completed.notice
            assert any(
                '"optionId": "' + choice + '"' in item.text for item in completed.activity.items
            )
            saved = service.permissions.page("harbor").items[0]
            assert saved.answer == choice and saved.released_at
            assert saved.options == pending.options and saved.details == pending.details
        finally:
            await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("immediate,flags", [(True, ()), (False, ()), (False, ("slow-start",))])
def test_stop_during_startup_or_stream_does_not_stop_worker(
    tmp_path, monkeypatch, immediate, flags
):
    service, conversation = setup(tmp_path, monkeypatch, scenario="wait", flags=flags)
    run = service.execution.claim("harbor", BASE, {BASE: set()})
    service.execution.started("harbor", run.id)

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, message())
        if not immediate:
            async with asyncio.timeout(10):
                while not service.coordinator.store.get("harbor", turn.id).native_started:
                    await asyncio.sleep(0.01)
            if not flags:
                async with asyncio.timeout(10):
                    while not service.coordinator.store.get("harbor", turn.id).activity.items:
                        await asyncio.sleep(0.02)
        stopped = await service.coordinator.stop("harbor", turn.id)
        assert stopped.status in ("stopped", "uncertain"), stopped
        assert service.execution.get("harbor", run.id).status == "running"
        assert service.execution.settings("harbor").enabled
        assert not service.permissions.turns
        assert service.coordinator.store.page("harbor", conversation.id).items[0].id == turn.id
        await service.coordinator.close()

    asyncio.run(exercise())


def test_uncertain_cleanup_blocks_new_work_until_explicit_recovery(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch, flags=("cleanup-uncertain",))

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, message())
        current = await settled(service, turn)
        assert current.status == "uncertain"
        with pytest.raises(ApplicationError):
            service.coordinator.send("harbor", conversation.id, message())
        recovered = service.coordinator.store.confirm_stopped("harbor", turn.id)
        assert recovered.status == "interrupted"
        assert service.coordinator.store.page("harbor", conversation.id).active is None
        with pytest.raises(ApplicationError):
            service.coordinator.store.confirm_stopped("harbor", turn.id)
        await service.close()

    asyncio.run(exercise())


def test_atomic_reservation_frozen_settings_and_bounded_late_output(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    store = service.coordinator.store

    def reserve(_):
        try:
            return store.reserve("harbor", conversation.id, message())[0]
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert results.count("coordinator_busy") == 1
    turn = next(r for r in results if not isinstance(r, str))
    AgentSettings(service.workspace).edit(
        "harbor",
        "coordinator",
        AgentSettingsEdit(
            expected_revision=2, selection=AgentChoice(model="different", effort="high")
        ),
        conversation.id,
    )
    assert store.get("harbor", turn.id).settings.choice.model == "test-model"
    store.write(
        "harbor",
        turn.id,
        [ActivityUpdate(key=str(i), kind="agent", text="x" * 10000) for i in range(200)],
    )
    saved = store.get("harbor", turn.id)
    assert sum(len(item.text) for item in saved.activity.items) <= MAX_TOTAL
    assert saved.activity.omitted
    store.restart()
    saved = store.get("harbor", turn.id)
    assert saved.status == "interrupted"
    store.write("harbor", turn.id, [ActivityUpdate(key="late", kind="agent", text="late")])
    assert store.get("harbor", turn.id) == saved
    with pytest.raises(ApplicationError):
        store.reserve("harbor", conversation.id, CoordinatorSend(id=turn.id, text="changed"))


def test_crash_recovery_never_replays(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    project = workspace.project("harbor")
    upgraded = Workspace(workspace.directory)
    assert (
        upgraded.schema_version == migrations.current_version()
        and upgraded.project("harbor") == project
    )
    service = Supervisor(upgraded)
    # No native launch or model selection occurs when history is read/created.
    conversation = service.coordinator.store.new("harbor")
    assert service.coordinator.store.page("harbor", conversation.id).items == []
    with pytest.raises(ApplicationError, match="model and effort"):
        service.coordinator.store.reserve("harbor", conversation.id, message())
    with upgraded.connection(write=True) as db:
        db.execute("UPDATE integration_settings SET data=json_set(data,'$.runtime','local')")
    # A separate configured fixture produces real native-start recovery evidence.
    other, chat = setup(tmp_path / "other", monkeypatch)
    turn, _ = other.coordinator.store.reserve("harbor", chat.id, message())
    with other.workspace.connection(write=True) as db:
        turn.native_started = True
        other.coordinator.store._save(db, turn)
    restored = CoordinatorStore(Workspace(other.workspace.directory))
    restored.restart()
    assert restored.get("harbor", turn.id).status == "uncertain"
    assert not other.coordinator.jobs


def test_browser_reads_are_model_free_and_cross_project_scoped(tmp_path, monkeypatch):
    execution = fixture(tmp_path)
    monkeypatch.setattr(
        "flowfield.adapters.codex_agent.command",
        lambda *args: pytest.fail("Read launched a harness"),
    )
    app = create_app(data_dir=execution.workspace.directory)
    with TestClient(app, base_url="http://localhost") as client:
        history = client.get("/api/projects/harbor/coordinator")
        assert history.status_code == 200 and history.json()["items"] == []
        assert client.get("/api/projects/harbor/coordinator?after=1").json()["items"] == []
        assert client.get("/api/projects/harbor/coordinator?after=0").status_code == 422
        assert client.get("/api/projects/harbor/coordinator?before=2&after=1").status_code == 400
        assert client.get("/api/projects/elsewhere/coordinator?after=1").status_code == 404
        conversation = client.post("/api/projects/harbor/coordinator").json()
        path = f"/api/projects/harbor/coordinator/{conversation['id']}"
        assert client.get(path).json()["items"] == []
        assert client.get(path.replace("harbor", "elsewhere")).status_code == 404
        assert client.post(path + "/messages", json=message().model_dump()).status_code == 409
        assert client.get(path + "/settings").status_code == 200


def test_history_pages_and_duplicate_retry_at_capacity(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    store = service.coordinator.store
    for index in range(23):
        request = message(f"Message {index}")
        turn, _ = store.reserve("harbor", conversation.id, request)
        with service.workspace.connection(write=True) as db:
            turn.status = "completed"
            store._save(db, turn)
    page = store.page("harbor", conversation.id)
    assert len(page.items) == 20 and page.items[-1].text == "Message 22"
    earlier = store.page("harbor", conversation.id, page.next_before)
    assert [turn.text for turn in earlier.items] == ["Message 0", "Message 1", "Message 2"]
    # Forward refresh includes the boundary turn, whose activity may still change,
    # and catches up in bounded ascending pages without skipping unseen turns.
    forward = store.page("harbor", after=1)
    assert [turn.number for turn in forward.items] == list(range(1, 21))
    forward = store.page("harbor", after=forward.items[-1].number)
    assert [turn.number for turn in forward.items] == [20, 21, 22, 23]
    assert forward.next_before is None
    assert store.page("harbor", after=24).items == []
    with pytest.raises(ApplicationError, match="either earlier or newer"):
        store.page("harbor", before=20, after=1)
    assert store.reserve("harbor", conversation.id, request, available=False)[1] is False
    with pytest.raises(ApplicationError, match="slots are busy"):
        store.reserve("harbor", conversation.id, message(), available=False)
    assert store.new("harbor").id == conversation.id


def test_scoped_coordinator_applies_saved_answer_without_granting_code_approval(tmp_path):
    from test_questions import ask
    from test_questions import setup as questions_setup

    from flowfield.agent_tools import coordinator_scope
    from flowfield.questions import QuestionAnswer

    workspace, questions = questions_setup(tmp_path)
    question = ask(questions)
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(
            expected_revision=question.revision, answer="All filtered rows, capped at 10,000."
        ),
    )

    async def exercise():
        grant = await coordinator_scope(Supervisor(workspace), "harbor")
        assert {"review_result", "answer_question", "configure_workers"} <= grant.tools.keys()
        assert "initialize_project" not in grant.tools
        result = await grant.call(
            "apply_answer",
            {
                "question_id": question.id,
                "application": {
                    "expected_revision": 2,
                    "expected_task_revision": 1,
                    "body": "Export all filtered rows, capped at 10,000.",
                    "decision": "Apply the human's saved row limit.",
                    "author": "human",
                },
            },
        )
        assert not result.isError, result
        assert workspace.task("harbor", "HAR-2").body.endswith("10,000.")
        assert workspace.task("harbor", "HAR-2").updated_by == "agent"
        grant.revoke()

    asyncio.run(exercise())


def test_long_unicode_reply_keeps_latest_exchange_in_bounded_context(tmp_path, monkeypatch):
    import json

    from flowfield.reads import size

    service, conversation = setup(tmp_path, monkeypatch)
    store = service.coordinator.store
    previous, _ = store.reserve(
        "harbor", conversation.id, message("Keep this requirement: " + "境" * 15000)
    )
    store.write(
        "harbor",
        previous.id,
        [
            ActivityUpdate(
                key=str(index),
                kind="agent",
                text=("Start of reply. " if index == 0 else "")
                + "界" * 5500
                + (" Final decision: preserve the API." if index == 9 else ""),
            )
            for index in range(10)
        ],
    )
    with service.workspace.connection(write=True) as db:
        previous = store._get(db, "harbor", previous.id)
        previous.status = "completed"
        store._save(db, previous)
    current, _ = store.reserve("harbor", conversation.id, message("Yes, proceed."))
    prompt = json.loads(service.coordinator._prompt(current, "scoped-tools"))
    assert len(prompt["recent_conversation"]) == 1
    retained = prompt["recent_conversation"][0]
    assert retained["human"].startswith("Keep this requirement:")
    assert retained["coordinator"].startswith("Start of reply.")
    assert retained["coordinator"].endswith("Final decision: preserve the API.")
    assert "omitted" in retained["coordinator"]
    assert size(prompt["recent_conversation"]) <= 48000
    assert prompt["history_is_partial"] is True
    assert prompt["human_message"] == "Yes, proceed."
    assert store.get("harbor", previous.id).activity == previous.activity


def test_task_focus_is_scoped_revision_bound_and_idempotent(tmp_path, monkeypatch):
    from flowfield.application import TaskEdit
    from flowfield.coordinator_models import CoordinatorTaskSelection

    service, conversation = setup(tmp_path, monkeypatch)
    ws, store = service.workspace, service.coordinator.store
    task = ws.create_task("harbor", task_request(title="Focused work", body="Original"))
    request = CoordinatorSend(
        id=uuid4().hex,
        text="Explain this task",
        task_context=CoordinatorTaskSelection(task_id=task.key, task_revision=task.revision),
    )
    turn, fresh = store.reserve("harbor", conversation.id, request)
    assert fresh and turn.task_context.task_id == task.id
    assert turn.task_context.title == task.title
    prompt = json.loads(service.coordinator._prompt(turn, "scoped-tools", resumed=True))
    assert prompt["selected_task"]["task_id"] == task.id
    changed = ws.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, title="New title")
    )
    assert store.reserve("harbor", conversation.id, request) == (turn, False)
    assert store.get("harbor", turn.id).task_context.title == "Focused work"
    with pytest.raises(ApplicationError, match="different content"):
        store.reserve("harbor", conversation.id, request.model_copy(update={"task_context": None}))
    with pytest.raises(ApplicationError, match="selected task changed"):
        store.reserve("harbor", conversation.id, request.model_copy(update={"id": uuid4().hex}))
    with pytest.raises(ApplicationError):
        store.reserve(
            "harbor",
            conversation.id,
            CoordinatorSend(
                id=uuid4().hex,
                text="Wrong reference",
                task_context=CoordinatorTaskSelection(task_id="missing-task", task_revision=1),
            ),
        )
    with ws.connection(write=True) as db:
        turn.status = "completed"
        store._save(db, turn)
    with pytest.raises(ApplicationError):
        store.reserve(
            "harbor",
            conversation.id,
            CoordinatorSend(
                id=uuid4().hex,
                text="Wrong result",
                task_context=CoordinatorTaskSelection(
                    task_id=task.id, task_revision=changed.revision, result_id="missing"
                ),
            ),
        )
    plain, _ = store.reserve("harbor", conversation.id, message("Discuss the project"))
    assert plain.task_context is None
    prompt = json.loads(service.coordinator._prompt(plain, "scoped-tools"))
    assert prompt["selected_task"] is None
    assert json.loads(prompt["recent_conversation"][0]["selected_task"])["task_id"] == task.id


def test_selected_result_is_exact_and_must_belong_to_the_task(tmp_path, monkeypatch):
    from test_execution import result

    from flowfield.coordinator_models import CoordinatorTaskSelection

    service, conversation = setup(tmp_path, monkeypatch)
    result(service.execution, process=False)
    selected = service.results.page("harbor", "task-0").items[0]
    task = service.workspace.task("harbor", "task-0")
    other = service.workspace.create_task("harbor", task_request(title="Other task"))
    store = service.coordinator.store
    with pytest.raises(ApplicationError, match="does not belong"):
        store.reserve(
            "harbor",
            conversation.id,
            CoordinatorSend(
                id=uuid4().hex,
                text="Explain",
                task_context=CoordinatorTaskSelection(
                    task_id=other.id, task_revision=other.revision, result_id=selected.id
                ),
            ),
        )
    request = CoordinatorSend(
        id=uuid4().hex,
        text="Explain this result",
        task_context=CoordinatorTaskSelection(
            task_id=task.id, task_revision=task.revision, result_id=selected.id
        ),
    )
    turn, _ = store.reserve("harbor", conversation.id, request)
    assert turn.task_context.result_id == selected.id
    assert service.results.page("harbor", task.id).items[0] == selected
    assert (
        json.loads(service.coordinator._prompt(turn, "tools", resumed=True))["selected_task"][
            "result_id"
        ]
        == selected.id
    )
    with pytest.raises(ApplicationError, match="different content"):
        store.reserve(
            "harbor",
            conversation.id,
            request.model_copy(
                update={"task_context": request.task_context.model_copy(update={"result_id": None})}
            ),
        )
