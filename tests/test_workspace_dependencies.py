"""One workspace dependency graph, independent repository execution and exact evidence."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from project_fixtures import existing_directory, task_request
from workspace_dependencies_fixtures import complete, shared

from flowfield.application import ProjectSetup, TaskEdit, TaskPublish
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.reads import ContextReads
from flowfield.workspaces import Workspaces


@pytest.mark.parametrize("destination", ["core", "site"])
def test_waiting_state_matches_workspace_prerequisites(tmp_path, destination):
    store = shared(tmp_path)
    space = Workspaces(store).for_project("core").id
    first = store.create_task(space, task_request(project_id="core", title="First"))
    second = store.create_task(
        space, task_request(project_id=destination, title="Then", dependencies=[first.key])
    )
    for task in (
        BrowserReads(store).task(space, second.id),
        next(t for t in BrowserReads(store).workspace_board(space).tasks if t.id == second.id),
    ):
        assert task.state.label == "Waiting on prerequisites"
        assert task.state.reason == "prerequisites"
        assert task.blocked_by[0].key == first.key
    complete(store, space, first.id)
    assert BrowserReads(store).task(space, second.id).state.reason is None


def test_same_local_ids_scope_cycles_pagination_and_downstream_reconciliation(tmp_path):
    store = shared(tmp_path)
    space = Workspaces(store).for_project("core").id
    core = store.create_task(
        space, task_request(project_id="core", id="core", title="API", body="API contract")
    )
    site = store.create_task(
        space,
        task_request(
            project_id="site",
            id="site",
            title="Site",
            body="Site behavior",
            dependencies=[core.key.lower()],
        ),
    )
    foreign_space = Workspaces(store).for_project("elsewhere").id
    foreign = store.create_task(
        foreign_space, task_request(project_id="elsewhere", id="same", title="Elsewhere")
    )
    assert site.dependencies == [core.key] and site.blocked_by[0].project_id == "core"
    with pytest.raises(ApplicationError) as rejected:
        store.edit_task(space, site.id, TaskEdit(expected_revision=1, dependencies=[foreign.id]))
    assert rejected.value.code == "invalid_dependency"
    with pytest.raises(ApplicationError) as cycle:
        store.edit_task(space, core.id, TaskEdit(expected_revision=1, dependencies=[site.key]))
    assert cycle.value.code == "dependency_cycle"
    assert store.task(space, core.id).revision == 1
    joined = store.create_task(
        space, task_request(project_id="core", title="Launch", dependencies=[core.key, site.key])
    )
    reads = ContextReads(store)
    before = reads.relationships(space, joined.id, limit=1)
    after = reads.relationships(space, joined.id, limit=1, after=before["next_cursor"])
    assert {before["items"][0]["project_id"], after["items"][0]["project_id"]} == {"core", "site"}
    assert after["next_cursor"] is None
    assert len(reads.tasks(space)["items"]) == 3
    assert sum(column["count"] for column in reads.overview(space)["columns"]) == 3
    assert len(BrowserReads(store).workspace_board(space).tasks) == 3
    assert BrowserReads(store).task(space, site.id).prerequisites[0].project_id == "core"
    notifications = []
    store.on_change = notifications.append
    complete(store, space, core.id)
    assert {space} == set(notifications)
    assert not store.task(space, site.id).blocked_by
    complete(store, space, site.id)
    notifications.clear()
    current = store.task(space, core.id)
    store.edit_task(
        space, core.id, TaskEdit(expected_revision=current.revision, body="Changed API contract")
    )
    revised = store.task(space, site.id)
    assert revised.reconciliation_reason and revised.body == "Site behavior"
    assert {space} == set(notifications)
    assert store.task(foreign_space, foreign.id).reconciliation_reason is None
    assert store.task(space, joined.id).blocked_by


def test_competing_cross_project_edges_cannot_commit_a_cycle(tmp_path):
    store = shared(tmp_path)
    space = Workspaces(store).for_project("core").id
    core = store.create_task(space, task_request(project_id="core", title="API"))
    site = store.create_task(space, task_request(project_id="site", title="Site"))

    def connect(task, target):
        try:
            store.edit_task(
                space, task.id, TaskEdit(expected_revision=1, dependencies=[target.key])
            )
            return "saved"
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(connect, core, site)
        second = pool.submit(connect, site, core)
        assert sorted([first.result(), second.result()]) == ["dependency_cycle", "saved"]


@pytest.mark.parametrize("managed", [False, True], ids=["frozen-scope", "managed-launch"])
def test_foreign_code_is_frozen_evidence_without_local_ancestry_or_worker_scope(
    tmp_path, monkeypatch, managed
):
    from result_fixtures import approve, current
    from result_fixtures import fixture as result_fixture

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.agent_models import AgentChoice
    from flowfield.agent_tools import worker_scope
    from flowfield.execution_models import QueueEdit, SettingsEdit
    from flowfield.integration_models import IntegrationConfig
    from flowfield.worker_tools import WorkerBridge

    service, _, _ = result_fixture(tmp_path)
    store = service.workspace
    service.results.process("harbor")
    accepted = approve(service, current(service))
    service.results.process("harbor")
    assert current(service).status == "delivered"
    repo = tmp_path / "site"
    Workspaces(store).adopt("harbor", ProjectSetup(path=existing_directory(repo)))
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("Independent website repository\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Site",
    )
    service.integrations.configure(
        "site",
        IntegrationConfig(
            expected_revision=1, runtime="local", target_branch="main", checks=["test -f README.md"]
        ),
    )
    task = store.create_task(
        "harbor",
        task_request(
            project_id="site",
            title="Use API",
            body="Review the delivered API",
            status="up_next",
            dependencies=["T-1"],
        ),
    )
    store.publish_task(
        "harbor", task.id, TaskPublish(expected_revision=task.revision, completion="code")
    )
    settings = service.execution.settings("harbor")
    if managed:
        settings = service.execution.configure(
            "harbor",
            SettingsEdit(
                expected_revision=settings.revision,
                selection=AgentChoice(model="test-model", effort="low"),
            ),
        )
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    own_base = baseline(repo)
    assert own_base != accepted.candidate_commit
    run = service.execution.claim("harbor", own_base, {own_base: set()})
    assert run and run.project_id == "site" and run.base_commit == own_base
    assignment = service.execution.assignment("harbor", run.id)
    prerequisites = json.loads(assignment["prerequisites"])
    assert prerequisites[0]["project_id"] == "harbor"
    assert prerequisites[0]["repository_scope"] == "other_repository"
    assert prerequisites[0]["commit"] == accepted.candidate_commit
    assert prerequisites[0]["result"]["summary"] == "Outcome"
    assert BrowserReads(store).workspace_board("harbor").pending_code.get(task.key, []) == []
    if managed:
        from fake_worker import FakeWorker

        monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", FakeWorker)
        monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture-process")
        asyncio.run(service._execute(run, repo))
        current_run = service.execution.get("harbor", run.id)
        assert current_run.status == "in_review", current_run.problem
        assert (service.environment(run.id).checkout / "result.txt").read_text() == "implemented"
        assert baseline(repo) == own_base  # Delivery still requires this result's approval.
        return
    service.execution.started("harbor", run.id)

    async def read_frozen_evidence():
        grant = worker_scope(WorkerBridge(service.execution, run, None))
        evidence = await grant.call("read_context", {"section": "prerequisites"})
        assert not evidence.isError
        assert "harbor" in str(evidence.content)
        forbidden = await grant.call("get_task", {"task_id": "work"})
        assert forbidden.isError
        assert forbidden.structuredContent["error"]["code"] == "operation_denied"
        grant.revoke()

    asyncio.run(read_frozen_evidence())
