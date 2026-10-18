"""Shared browser collections retain project identity and bounded attention reads."""

import asyncio
from unittest.mock import patch

import pytest
from coordinator_generations_fixtures import start_coordinator
from execution_fixtures import fixture
from fastapi.testclient import TestClient
from permissions_fixtures import OPTIONS
from project_fixtures import task_request
from workspace_dependencies_fixtures import shared

from flowfield.api import create_app
from flowfield.attention import attention_page
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.permission_models import PermissionAnswer
from flowfield.permissions import Permissions
from flowfield.questions import QuestionAnswer, QuestionCreate, Questions
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_shared_board_and_attention_keep_owner_scope_and_same_local_ids(tmp_path):
    store = shared(tmp_path)
    space = Workspaces(store).for_project("core").id
    for project in ("core", "site", "elsewhere"):
        owner = Workspaces(store).for_project(project).id
        store.create_task(
            owner,
            task_request(project_id=project, id=project, title=project, body="private body" * 100),
        )
        Questions(store).ask(
            owner,
            QuestionCreate(
                id=project,
                question=project + " question",
                context="long context" * 100,
                recommendation="Choose",
                task_id=project,
            ),
        )
    with patch.object(store, "_task", side_effect=AssertionError("Full task hydrated")):
        board = BrowserReads(store).workspace_board(space)
    assert {task.key for task in board.tasks} == {"T-1", "T-2"}
    assert set(board.pending_code) == {"T-1", "T-2"}
    assert board.needs_you_count == 2
    assert board.attention_work_keys == ["T-1", "T-2"]
    assert board.attention_requests == {
        "T-1": f"/workspaces/{space}/requests/question/core",
        "T-2": f"/workspaces/{space}/requests/question/site",
    }
    assert "private body" not in board.model_dump_json()
    first = attention_page(store, space, "action", limit=1)
    second = attention_page(store, space, "action", offset=first.next_offset, limit=1)
    assert first.total == second.total == 2
    assert {first.items[0].task_key, second.items[0].task_key} == {"T-1", "T-2"}
    assert "long context" not in first.model_dump_json()
    assert second.next_offset is None
    filtered = attention_page(store, space, "action", project_id="core")
    assert filtered.total == 1 and filtered.items[0].task_key == "T-1"
    with pytest.raises(ApplicationError) as rejected:
        attention_page(store, space, "action", project_id="elsewhere")
    assert rejected.value.code == "scope_mismatch"
    empty = Workspaces(store).create(WorkspaceCreate(name="Empty"))
    assert BrowserReads(store).workspace_board(empty.id).tasks == []
    assert attention_page(store, empty.id, "action").total == 0
    with TestClient(create_app(data_dir=store.directory), base_url="http://localhost") as client:
        response = client.get(f"/api/workspaces/{space}/view/board")
        assert response.status_code == 200 and len(response.json()["tasks"]) == 2
        assert client.get(f"/api/workspaces/{space}/view/attention?limit=1").json()["total"] == 2
        assert client.get(f"/api/workspaces/{space}/view/attention?limit=101").status_code == 422
    Questions(store).answer(
        space, "core", QuestionAnswer(expected_revision=1, answer="Use the local scope")
    )
    assert BrowserReads(store).workspace_board(space).attention_work_keys == ["T-2"]
    assert set(BrowserReads(store).workspace_board(space).attention_requests) == {"T-2"}
    Questions(store).ask(
        space,
        QuestionCreate(
            id="newer",
            task_id="site",
            question="New question",
            context="Choose again",
            recommendation="Yes",
        ),
    )
    assert BrowserReads(store).workspace_board(space).attention_requests["T-2"] == (
        f"/workspaces/{space}/requests/question/newer"
    )
    Questions(store).answer(space, "newer", QuestionAnswer(expected_revision=1, answer="Yes"))
    assert BrowserReads(store).workspace_board(space).attention_requests["T-2"] == (
        f"/workspaces/{space}/requests/question/site"
    )


def test_workspace_inbox_includes_coordinator_permissions_without_project_duplication(tmp_path):
    async def exercise():
        store = fixture(tmp_path).workspace
        space = "harbor"
        _, coordinator, generation = start_coordinator(store)
        permissions = Permissions(store)
        async with permissions.turn(
            space,
            "coordinator",
            session_id="native",
            turn_id=coordinator.id,
            conversation_id=coordinator.conversation_id,
            generation_id=generation.id,
        ) as turn:
            pending = asyncio.create_task(
                turn.request("tool", "Inspect member repository", OPTIONS)
            )
            async with asyncio.timeout(4):
                while not (items := permissions.page(space).pending):
                    await asyncio.sleep(0.01)
            page = attention_page(store, space, "action")
            assert page.total == 1 and page.items[0].task_key is None
            assert page.items[0].state.href == f"/workspaces/{space}/inbox"
            assert BrowserReads(store).workspace_board(space).needs_you_count == 1
            assert attention_page(store, space, "action", project_id="harbor").total == 0
            permissions.answer(
                space,
                items[0].id,
                PermissionAnswer(expected_revision=1, option_id="no"),
            )
            assert await pending == "no"
            assert attention_page(store, space, "action").total == 0
            assert attention_page(store, space, "history").items[0].status == "answered"

    asyncio.run(exercise())
