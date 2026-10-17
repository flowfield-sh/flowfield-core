"""Organization changes cannot change submitted provenance or retained bytes."""

import pytest
from file_delivery_fixtures import deliver, setup, task
from pydantic import ValidationError

from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.file_browser import FileBrowser, FileFolder, FileLocation
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
    browser.folder(owner.id, FileFolder(path="Release/Final"))
    assert browser.directory(owner.id, "Release").items[0].name == "Final"
    assert browser.directory(owner.id, "Release/Final").items == []
    output = result.files[0]
    moved = browser.move(
        owner.id, output.id, FileLocation(path="Release/Final/launch.txt", expected_revision=1)
    )
    assert moved.revision == 2
    assert [e.name for e in browser.directory(owner.id, "Release/Final").items] == ["launch.txt"]
    assert Outputs(store).get(owner.id, output.id).name == "reports/brief.txt"
    assert Results(store).get(scope, result.id).files[0].name == "reports/brief.txt"
    assert Outputs(store).content(output).read_text() == "first"
    with pytest.raises(ApplicationError):
        browser.move(owner.id, output.id, FileLocation(path="old.txt", expected_revision=1))
    other = Workspaces(store).create(WorkspaceCreate(name="Other"))
    with pytest.raises(ApplicationError, match="File not found"):
        browser.move(other.id, output.id, FileLocation(path="stolen.txt", expected_revision=2))
    assert browser.directory(other.id).items == []
    restarted = FileBrowser(Workspace(store.directory))
    assert restarted.directory(owner.id, "Release/Final").items[0].revision == 2


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a//b", "a/./b", "bad\\path", "a\nb"])
def test_organization_rejects_invalid_paths(path):
    with pytest.raises(ValidationError):
        FileFolder(path=path)
    with pytest.raises(ValidationError):
        FileLocation(path=path, expected_revision=1)


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
