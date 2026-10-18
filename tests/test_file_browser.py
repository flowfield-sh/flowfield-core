"""Submitted folders, task recency and retained bytes share one file browser."""

import pytest
from file_delivery_fixtures import deliver, setup, task
from workspace_fixtures import TEST_AGENTS

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.file_browser import FileBrowser
from flowfield.outputs import Outputs
from flowfield.results import Results
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_folders_filter_work_and_preserve_submitted_history(tmp_path):
    store, owner, scope = setup(tmp_path / "state")
    first = task(store, scope)
    result = deliver(store, scope, first.id, {"reports/brief.txt": "first", "image.png": "pixels"})
    second = task(store, scope, title="Another task")
    deliver(store, scope, second.id, {"reports/second.txt": "second"})
    browser = FileBrowser(store)
    assert [(e.name, e.kind) for e in browser.directory(owner.id).items] == [
        ("reports", "folder"),
        ("image.png", "file"),
    ]
    assert [e.name for e in browser.directory(owner.id, "reports", task_key=first.key).items] == [
        "brief.txt"
    ]
    assert len(browser.work(owner.id).items) == 2
    assert browser.work(owner.id).items[0].task_key == second.key
    output = result.files[0]
    assert Outputs(store).content(output).read_text() == "first"
    assert Results(store).get(scope, result.id).files[0].name == "reports/brief.txt"
    other = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    assert browser.directory(other.id).items == []
    restarted = FileBrowser(Workspace(store.directory))
    assert restarted.directory(owner.id, "reports").items[0].name == "brief.txt"


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a//b", "a/./b", "bad\\path", "a\nb"])
def test_browser_rejects_invalid_paths(tmp_path, path):
    store, owner, _ = setup(tmp_path / "state")
    with pytest.raises(ApplicationError, match="relative folder"):
        FileBrowser(store).directory(owner.id, path)


def test_directory_pagination_and_restart_preserve_files(tmp_path):
    store, owner, scope = setup(tmp_path / "state")
    for index in range(2):
        current = task(store, scope, title=f"Task {index}")
        deliver(
            store,
            scope,
            current.id,
            {f"folder-{index * 30 + n:02}/item.txt": "content" for n in range(30)},
        )
    browser = FileBrowser(store)
    page = browser.directory(owner.id)
    assert len(page.items) == 50 and page.next_offset == 50
    rest = browser.directory(owner.id, offset=page.next_offset)
    assert len(rest.items) == 10 and rest.next_offset is None
    assert not {e.id for e in page.items} & {e.id for e in rest.items}
    restarted = Workspace(store.directory)
    assert len(FileBrowser(restarted).directory(owner.id).items) == 50
    with restarted.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
