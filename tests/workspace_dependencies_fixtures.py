"""Reusable isolated setup and scripted peers; no collected test cases."""

from project_fixtures import existing_directory

from flowfield.application import ProjectSetup, TaskProgress, Workspace
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
