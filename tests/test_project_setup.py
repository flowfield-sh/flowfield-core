"""Project entry preserves files, resolves identity, and shares service state."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.project_config import discover_project, read_config
from flowfield.state import data_path


def test_init_preserves_files_and_registered_identity(tmp_path: Path) -> None:
    root = tmp_path / "Harbor Project"
    root.mkdir()
    (root / "AGENTS.md").write_text("Existing instructions\n")
    (root / "dirty.py").write_text("unfinished changes\n")
    workspace = Workspace(tmp_path / "state")
    original = workspace.setup_project(
        ProjectSetup(id="harbor", name='Harbor "expenses" 🚢', path=str(root))
    )
    initialized = workspace.setup_project(ProjectSetup(path=str(root)))
    assert initialized == original
    config = root / ".flowfield/config.toml"
    before = config.read_bytes()
    assert read_config(root).project_id == "harbor"
    assert read_config(root).name == original.name
    assert str(root) not in config.read_text()
    assert workspace.setup_project(ProjectSetup(path=str(root))) == initialized
    assert config.read_bytes() == before
    assert (root / "AGENTS.md").read_text() == "Existing instructions\n"
    assert (root / "dirty.py").read_text() == "unfinished changes\n"
    assert sorted(p.name for p in (root / ".flowfield").iterdir()) == ["config.toml"]
    fresh = Workspace(tmp_path / "fresh-state")
    assert fresh.setup_project(ProjectSetup(path=str(root))).id == original.id
    assert config.read_bytes() == before


def test_adopt_and_conflicts_do_not_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "harbor"
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        space = client.post("/api/workspaces", json={"name": "Fixture"}).json()["id"]
        assert client.post("/api/projects/create", json={"path": str(root)}).status_code == 404
        assert not root.exists()
        root.mkdir()
        created = client.post(f"/api/workspaces/{space}/projects", json={"path": str(root)})
        assert created.status_code == 201
        assert created.json()["id"] == created.json()["name"] == "harbor"
        assert read_config(root).project_id == "harbor"
        assert (
            client.post(f"/api/workspaces/{space}/projects", json={"path": str(root)}).json()
            == created.json()
        )
        config = root / ".flowfield/config.toml"
        before = config.read_bytes()
        assert (
            client.post(
                f"/api/workspaces/{space}/projects", json={"path": str(root), "id": "different"}
            ).status_code
            == 409
        )
        assert config.read_bytes() == before
        other = tmp_path / "other"
        other.mkdir()
        assert (
            client.post(
                f"/api/workspaces/{space}/projects", json={"path": str(other), "id": "harbor"}
            ).status_code
            == 409
        )
        assert not (other / ".flowfield").exists()
        assert (
            client.post(
                f"/api/workspaces/{space}/projects", json={"path": str(other / "missing")}
            ).status_code
            == 400
        )
        assert not (other / ".flowfield").exists()
        assert (
            client.post(f"/api/workspaces/{space}/projects", json={"path": "relative"}).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/workspaces/{space}/projects",
                json={"path": str(other)},
                headers={"origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert not (other / ".flowfield").exists()


@pytest.mark.parametrize(
    "content",
    [
        "not toml!",
        'version = 2\nproject_id = "harbor"\nname = "Harbor"',
        'version = 1\nproject_id = "../oops"\nname = "Harbor"',
    ],
)
def test_invalid_config_preserved(tmp_path: Path, content: str) -> None:
    root = tmp_path / "project"
    (root / ".flowfield").mkdir(parents=True)
    file = root / ".flowfield/config.toml"
    file.write_text(content)
    workspace = Workspace(tmp_path / "state")
    with pytest.raises(ApplicationError, match="Fix the config"):
        workspace.setup_project(ProjectSetup(path=str(root)))
    assert file.read_text() == content
    assert workspace.projects() == []


def test_conflicting_config_locations_and_nested_discovery(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    root = tmp_path / "harbor"
    root.mkdir()
    root.mkdir(exist_ok=True)
    workspace.setup_project(ProjectSetup(path=str(root)))
    subdir = root / "src" / "deep"
    subdir.mkdir(parents=True)
    assert discover_project(subdir) == "harbor"
    (subdir / ".git").mkdir()
    with pytest.raises(ApplicationError, match="No project config"):
        discover_project(subdir)
    workspace.setup_project(ProjectSetup(path=str(subdir), id="nested"))
    assert discover_project(subdir) == "nested"
    other = tmp_path / "other"
    other.mkdir()
    (other / ".flowfield").write_text("Keep this file")
    with pytest.raises(ApplicationError, match="regular directory"):
        workspace.setup_project(ProjectSetup(path=str(other)))
    assert (other / ".flowfield").read_text() == "Keep this file"
    (other / ".flowfield").unlink()
    (other / ".flowfield").symlink_to(root / ".flowfield", target_is_directory=True)
    with pytest.raises(ApplicationError, match="regular directory"):
        workspace.setup_project(ProjectSetup(path=str(other)))
    assert read_config(root).project_id == "harbor"


def test_global_home_and_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWFIELD_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert data_path() == tmp_path / ".flowfield"
    assert not data_path().exists()
    monkeypatch.setenv("FLOWFIELD_DATA_DIR", str(tmp_path / "isolated"))
    assert data_path() == tmp_path / "isolated"
    workspace = Workspace(tmp_path / ".flowfield")
    with pytest.raises(ApplicationError, match="global data directory"):
        workspace.setup_project(ProjectSetup(path=str(tmp_path)))


def test_interrupted_setup_can_finish_from_preserved_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import flowfield.application as application

    root = tmp_path / "harbor"
    root.mkdir()
    workspace = Workspace(tmp_path / "state")
    original_write = application.write_config

    def interrupted(*args: object) -> None:
        original_write(*args)
        raise OSError("Simulated failure after config publication")

    with monkeypatch.context() as patch:
        patch.setattr(application, "write_config", interrupted)
        with pytest.raises(ApplicationError, match="try again"):
            workspace.setup_project(ProjectSetup(path=str(root)))
    assert workspace.projects() == []
    config = (root / ".flowfield/config.toml").read_bytes()
    assert workspace.setup_project(ProjectSetup(path=str(root))).id == "harbor"
    assert (root / ".flowfield/config.toml").read_bytes() == config
