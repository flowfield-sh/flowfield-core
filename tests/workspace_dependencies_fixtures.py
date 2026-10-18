"""Reusable isolated setup and scripted peers; no collected test cases."""

from lifecycle_fixtures import run_report
from project_fixtures import existing_directory
from workspace_fixtures import TEST_AGENTS

from flowfield.application import ProjectSetup, Workspace
from flowfield.workspaces import WorkspaceCreate, Workspaces


def shared(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    other = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    for project, owner in (("core", space.id), ("site", space.id), ("elsewhere", other.id)):
        spaces.adopt(owner, ProjectSetup(path=existing_directory(tmp_path / project)))
    return store


def complete(store, project, identity):
    task = store.task(project, identity)
    return run_report(
        store, project, task.id, expected_revision=task.revision, status="done", completion="report"
    )
