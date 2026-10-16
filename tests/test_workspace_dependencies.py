"""One workspace dependency graph, independent repository execution and exact evidence."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from project_fixtures import existing_directory, task_request
from storage_fixtures import logical_data

from flowfield import migrations
from flowfield.application import ProjectSetup, TaskEdit, TaskProgress, TaskPublish, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.migrations import Migration
from flowfield.reads import ContextReads
from flowfield.workspaces import WorkspaceCreate, Workspaces


def shared(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    other = spaces.create(WorkspaceCreate(name="Other"))
    for project, owner in (("core", space.id), ("site", space.id), ("elsewhere", other.id)):
        spaces.adopt(owner, ProjectSetup(path=existing_directory(tmp_path / project)))
    return store


def complete(store, project, identity):
    task = store.task(project, identity)
    return store.record_progress(
        project,
        task.id,
        TaskProgress(expected_revision=task.revision, status="done", completion="report"),
    )


@pytest.mark.parametrize("destination", ["core", "site"])
def test_waiting_state_matches_workspace_prerequisites(tmp_path, destination):
    store = shared(tmp_path)
    first = store.create_task("core", task_request(title="First"))
    second = store.create_task(destination, task_request(title="Then", dependencies=[first.key]))
    for task in (
        BrowserReads(store).task(destination, second.id),
        next(t for t in BrowserReads(store).board(destination).tasks if t.id == second.id),
    ):
        assert task.state.label == "Waiting on prerequisites"
        assert task.state.reason == "prerequisites"
        assert task.blocked_by[0].key == first.key
    complete(store, "core", first.id)
    assert BrowserReads(store).task(destination, second.id).state.reason is None


def test_same_local_ids_scope_cycles_pagination_and_downstream_reconciliation(tmp_path):
    store = shared(tmp_path)
    core = store.create_task("core", task_request(id="same", title="API", body="API contract"))
    site = store.create_task(
        "site",
        task_request(
            id="same", title="Site", body="Site behavior", dependencies=[core.key.lower()]
        ),
    )
    foreign = store.create_task("elsewhere", task_request(id="same", title="Elsewhere"))
    assert site.dependencies == [core.key] and site.blocked_by[0].project_id == "core"
    with pytest.raises(ApplicationError) as rejected:
        store.edit_task("site", site.id, TaskEdit(expected_revision=1, dependencies=[foreign.key]))
    assert rejected.value.code == "invalid_dependency"
    with pytest.raises(ApplicationError) as cycle:
        store.edit_task("core", core.id, TaskEdit(expected_revision=1, dependencies=[site.key]))
    assert cycle.value.code == "dependency_cycle"
    assert store.task("core", core.id).revision == 1
    joined = store.create_task(
        "core", task_request(title="Launch", dependencies=[core.key, site.key])
    )
    reads = ContextReads(store)
    before = reads.relationships("core", joined.id, limit=1)
    after = reads.relationships("core", joined.id, limit=1, after=before["next_cursor"])
    assert {before["items"][0]["project_id"], after["items"][0]["project_id"]} == {"core", "site"}
    assert after["next_cursor"] is None
    assert len(reads.tasks("site")["items"]) == 1
    assert sum(column["count"] for column in reads.overview("core")["columns"]) == 2
    assert len(BrowserReads(store).board("site").tasks) == 1
    assert BrowserReads(store).task("site", site.id).prerequisites[0].project_id == "core"
    notifications = []
    store.on_change = notifications.append
    complete(store, "core", core.id)
    assert {"core", "site"} == set(notifications)
    assert not store.task("site", site.id).blocked_by
    complete(store, "site", site.id)
    notifications.clear()
    current = store.task("core", core.id)
    store.edit_task(
        "core", core.id, TaskEdit(expected_revision=current.revision, body="Changed API contract")
    )
    revised = store.task("site", site.id)
    assert revised.reconciliation_reason and revised.body == "Site behavior"
    assert {"core", "site"} == set(notifications)
    assert store.task("elsewhere", foreign.id).reconciliation_reason is None
    assert store.task("core", joined.id).blocked_by


def test_competing_cross_project_edges_cannot_commit_a_cycle(tmp_path):
    store = shared(tmp_path)
    core = store.create_task("core", task_request(title="API"))
    site = store.create_task("site", task_request(title="Site"))

    def connect(task, target):
        try:
            store.edit_task(
                task.project_id, task.id, TaskEdit(expected_revision=1, dependencies=[target.key])
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
        "site",
        task_request(
            title="Use API",
            body="Review the delivered API",
            status="up_next",
            dependencies=["HAR-1"],
        ),
    )
    store.publish_task(
        "site", task.id, TaskPublish(expected_revision=task.revision, completion="code")
    )
    settings = service.execution.settings("site")
    if managed:
        settings = service.execution.configure(
            "site",
            SettingsEdit(
                expected_revision=settings.revision,
                selection=AgentChoice(model="test-model", effort="low"),
            ),
        )
    service.execution.queue("site", QueueEdit(expected_revision=settings.revision, enabled=True))
    own_base = baseline(repo)
    assert own_base != accepted.candidate_commit
    run = service.execution.claim("site", own_base, {own_base: set()})
    assert run and run.project_id == "site" and run.base_commit == own_base
    assignment = service.execution.assignment("site", run.id)
    prerequisites = json.loads(assignment["prerequisites"])
    assert prerequisites[0]["project_id"] == "harbor"
    assert prerequisites[0]["repository_scope"] == "other_repository"
    assert prerequisites[0]["commit"] == accepted.candidate_commit
    assert prerequisites[0]["result"]["summary"] == "Outcome"
    assert BrowserReads(store).board("site").pending_code.get(task.id, []) == []
    if managed:
        from fake_worker import FakeWorker

        monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", FakeWorker)
        monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture-process")
        asyncio.run(service._execute(run, repo))
        current_run = service.execution.get("site", run.id)
        assert current_run.status == "in_review", current_run.problem
        assert (service.environment(run.id).checkout / "result.txt").read_text() == "implemented"
        assert baseline(repo) == own_base  # Delivery still requires this result's approval.
        return
    service.execution.started("site", run.id)

    async def read_frozen_evidence():
        grant = worker_scope(WorkerBridge(service.execution, run, None))
        evidence = await grant.call("read_context", {"section": "prerequisites"})
        assert not evidence.isError
        assert "harbor" in str(evidence.content)
        forbidden = await grant.call("get_task", {"project_id": "harbor", "task_id": "work"})
        assert forbidden.isError
        assert forbidden.structuredContent["error"]["code"] == "operation_denied"
        grant.revoke()

    asyncio.run(read_frozen_evidence())


def test_upgrade_keeps_dependency_targets_agreements_and_history(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 62)
        )
        store = shared(tmp_path)
        first = store.create_task("core", task_request(id="old", title="API"))
        second = store.create_task("core", task_request(title="Ship", dependencies=[first.id]))
        original = second.model_dump()
    before = logical_data(store.directory)
    upgrade = next(m for m in migrations.MIGRATIONS if m.version == 62)

    def fail(db):
        upgrade.apply(db)
        raise ValueError("fixture dependency migration failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(62, fail) if m.version == 62 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    upgraded = Workspace(store.directory)
    migrated = upgraded.task("core", second.id)
    assert migrated.dependencies == [first.key]
    assert migrated.revision == original["revision"]
    assert migrated.agreement_revision == original["agreement_revision"]
    assert migrated.prerequisites[0].id == first.id
    assert migrated.revisions[0].dependencies == [first.key]
    assert migrated.blocked_by[0].project_id == "core"
