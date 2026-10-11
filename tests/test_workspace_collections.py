"""Shared browser collections retain project identity and bounded attention reads."""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from project_fixtures import task_request
from test_coordinator_generations import start_coordinator
from test_execution import fixture
from test_permissions import OPTIONS
from test_workspace_dependencies import shared

from flowfield.api import create_app
from flowfield.application import MilestoneCreate
from flowfield.attention import attention_page
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.milestones import Milestones
from flowfield.permission_models import PermissionAnswer
from flowfield.permissions import Permissions
from flowfield.questions import QuestionCreate, Questions
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_shared_board_and_attention_keep_owner_scope_and_same_local_ids(tmp_path):
    store = shared(tmp_path)
    space = Workspaces(store).for_project("core").id
    milestone = Milestones(store).create(space, MilestoneCreate(title="Launch"))
    for project in ("core", "site", "elsewhere"):
        store.create_task(
            project, task_request(id="same", title=project, body="private body" * 100)
        )
        Questions(store).ask(
            project,
            QuestionCreate(
                id="same",
                question=project + " question",
                context="long context" * 100,
                recommendation="Choose",
                task_id="same",
            ),
        )
    with patch.object(store, "_task", side_effect=AssertionError("Full task hydrated")):
        board = BrowserReads(store).workspace_board(space)
    assert {task.key for task in board.tasks} == {"COR-1", "SIT-1"}
    assert set(board.pending_code) == {"COR-1", "SIT-1"}
    assert board.milestones == [milestone]
    assert board.needs_you_count == 2
    assert "private body" not in board.model_dump_json()
    first = attention_page(store, None, "action", limit=1, workspace_id=space)
    second = attention_page(
        store, None, "action", offset=first.next_offset, limit=1, workspace_id=space
    )
    assert first.total == second.total == 2
    assert {first.items[0].project_id, second.items[0].project_id} == {"core", "site"}
    assert "long context" not in first.model_dump_json()
    assert second.next_offset is None
    filtered = attention_page(store, "core", "action", workspace_id=space)
    assert filtered.total == 1 and filtered.items[0].project_id == "core"
    with pytest.raises(ApplicationError) as rejected:
        attention_page(store, "elsewhere", "action", workspace_id=space)
    assert rejected.value.code == "scope_mismatch"
    empty = Workspaces(store).create(WorkspaceCreate(name="Empty"))
    assert BrowserReads(store).workspace_board(empty.id).tasks == []
    assert attention_page(store, None, "action", workspace_id=empty.id).total == 0
    with TestClient(create_app(data_dir=store.directory), base_url="http://localhost") as client:
        response = client.get(f"/api/workspaces/{space}/view/board")
        assert response.status_code == 200 and len(response.json()["tasks"]) == 2
        assert client.get(f"/api/workspaces/{space}/view/attention?limit=1").json()["total"] == 2
        assert client.get(f"/api/workspaces/{space}/view/attention?limit=101").status_code == 422


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
                while not (items := permissions.page(space, workspace_scope=True).pending):
                    await asyncio.sleep(0.01)
            page = attention_page(store, None, "action", workspace_id=space)
            assert page.total == 1 and page.items[0].project_id is None
            assert page.items[0].state.href == f"/workspaces/{space}/inbox"
            assert BrowserReads(store).workspace_board(space).needs_you_count == 1
            assert attention_page(store, "harbor", "action", workspace_id=space).total == 0
            permissions.answer(
                space,
                items[0].id,
                PermissionAnswer(expected_revision=1, option_id="no"),
                workspace_scope=True,
            )
            assert await pending == "no"
            assert attention_page(store, None, "action", workspace_id=space).total == 0
            assert (
                attention_page(store, None, "history", workspace_id=space).items[0].status
                == "answered"
            )

    asyncio.run(exercise())
