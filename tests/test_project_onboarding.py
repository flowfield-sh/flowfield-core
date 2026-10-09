"""Native selection is explicit, bounded and separate from project/worker setup."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from flowfield.adapters import directory_picker
from flowfield.agent_models import AgentChoice
from flowfield.agent_settings import AgentSettings
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption
from flowfield.integration import Integrations


def test_creation_validates_and_saves_both_agent_defaults(tmp_path, monkeypatch):
    root = tmp_path / "agents-ready"
    root.mkdir()
    calls = []

    async def discover(directory, *, cwd, registration, on_cleanup, **kwargs):
        calls.append((cwd, registration.harness))
        on_cleanup(True)
        return [ModelOption(id="fixture", name="Fixture", efforts=["low"])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        choice = {"harness": "codex", "model": "fixture", "effort": "low"}
        setup = {"path": str(root), "coordinator": choice, "worker": choice, "max_parallel": 3}
        models = client.get("/api/worker-models", params={"project_path": str(root)})
        assert models.status_code == 200
        assert calls == [(root, "codex")]
        assert not (root / ".flowfield").exists()
        assert client.get("/api/projects").json() == []
        failed = client.post(
            "/api/projects/initialize", json={**setup, "worker": {**choice, "model": "missing"}}
        )
        assert failed.status_code == 409
        assert client.get("/api/projects").json() == []
        assert not (root / ".flowfield").exists()
        created = client.post("/api/projects/initialize", json=setup)
        assert created.status_code == 200, created.text
        assert len(calls) == 1  # Same pre-adoption scope reuses owned discovery.
        project = created.json()["id"]
        worker = client.get(f"/api/projects/{project}/workers").json()
        assert worker["selection"]["model"] == "fixture"
        assert worker["max_parallel"] == 3
        assert worker["enabled"] is False
        coordinator = AgentSettings(client.app.state.workspace).get(project, "coordinator")
        assert coordinator.selection.model == "fixture"
        assert coordinator.selection.mode == "read-only"
        # Re-adopting a registered project cannot overwrite its settings.
        assert client.post("/api/projects/initialize", json={"path": str(root)}).status_code == 200
        assert client.post("/api/projects/initialize", json=setup).status_code == 200
        assert (
            client.post("/api/projects/initialize", json={**setup, "max_parallel": 2}).status_code
            == 409
        )
        assert client.get(f"/api/projects/{project}/workers").json() == worker
        invalid_scope = client.get(
            "/api/worker-models", params={"project_id": project, "project_path": str(root)}
        )
        assert invalid_scope.status_code == 400
        assert (
            client.get(
                "/api/worker-models", params={"project_path": str(root / "missing")}
            ).status_code
            == 400
        )


def test_failed_agent_setup_rolls_back_project_and_both_settings(tmp_path):
    root = tmp_path / "rollback"
    root.mkdir()
    workspace = Workspace(tmp_path / "state")
    choice = AgentChoice(model="fixture", effort="low")
    with pytest.raises(ApplicationError, match="individual task"):
        workspace.setup_project(
            ProjectSetup(
                path=str(root), coordinator=choice, worker=choice.model_copy(update={"fast": True})
            )
        )
    assert workspace.projects() == []
    assert not (root / ".flowfield").exists()
    with workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM agent_settings").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM worker_settings").fetchone()[0] == 0


def test_picker_selection_cancel_and_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Project with spaces 🚢"
    root.mkdir()
    monkeypatch.setattr(directory_picker.sys, "platform", "darwin")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, str(root) + "/\n", ""))
    monkeypatch.setattr(directory_picker.subprocess, "run", run)
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        assert (
            client.post(
                "/api/projects/select-directory", headers={"origin": "https://elsewhere.example"}
            ).status_code
            == 403
        )
        run.assert_not_called()
        selected = client.post("/api/projects/select-directory")
        assert selected.json() == {"path": str(root)}
        assert client.get("/api/projects").json() == []
        assert not (root / ".flowfield").exists()
        assert run.call_args.kwargs["timeout"] == directory_picker.TIMEOUT
        run.return_value = subprocess.CompletedProcess([], 0, "\n", "")
        assert client.post("/api/projects/select-directory").json() == {"path": None}
        assert client.get("/api/projects").json() == []


def test_picker_failure_timeout_busy_and_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(directory_picker, "picker_command", lambda: ["fake-picker"])
    run = Mock(side_effect=subprocess.TimeoutExpired("fake-picker", 300))
    monkeypatch.setattr(directory_picker.subprocess, "run", run)
    with pytest.raises(ApplicationError, match="timed out"):
        directory_picker.select_directory()
    run.side_effect = OSError("missing executable")
    with pytest.raises(ApplicationError, match="project init"):
        directory_picker.select_directory()
    run.side_effect = None
    run.return_value = subprocess.CompletedProcess([], 2, "", "private diagnostic")
    with pytest.raises(ApplicationError, match="Could not select"):
        directory_picker.select_directory()
    run.return_value = subprocess.CompletedProcess([], 0, str(tmp_path / "missing"), "")
    with pytest.raises(ApplicationError, match="existing project"):
        directory_picker.select_directory()
    assert directory_picker._selection.acquire(blocking=False)
    try:
        with pytest.raises(ApplicationError, match="already open"):
            directory_picker.select_directory()
    finally:
        directory_picker._selection.release()


def test_desktop_commands_and_headless_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(directory_picker.sys, "platform", "win32")
    assert "-STA" in directory_picker.picker_command()
    monkeypatch.setattr(directory_picker.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(
        directory_picker.shutil, "which", lambda name: name if name == "zenity" else None
    )
    assert directory_picker.picker_command()[:3] == ["zenity", "--file-selection", "--directory"]
    monkeypatch.setattr(
        directory_picker.shutil, "which", lambda name: name if name == "kdialog" else None
    )
    assert directory_picker.picker_command()[:2] == ["kdialog", "--getexistingdirectory"]
    monkeypatch.setattr(
        directory_picker.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "", "")),
    )
    assert directory_picker.select_directory().path is None
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(ApplicationError, match="project init"):
        directory_picker.picker_command()


def test_local_is_automatic_before_git_or_delivery(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(tmp_path / "state")
    workspace.setup_project(ProjectSetup(path=str(root)))
    settings = Integrations(workspace).settings("project")
    assert settings.runtime == "local" and settings.revision == 1
    assert settings.checks == [] and settings.target_branch is None
    assert not (root / ".git").exists()
    assert Integrations(Workspace(workspace.directory)).settings("project") == settings
    with TestClient(
        create_app(data_dir=workspace.directory), base_url="http://localhost"
    ) as client:
        assert client.get("/api/projects/project/integration").json()["runtime"] == "local"
        assert client.post("/api/projects/project/integration/local", json={}).status_code == 404


def test_setup_defaults_are_read_only_and_preserve_existing_identity(tmp_path: Path) -> None:
    root = tmp_path / "Project with spaces 🚢"
    root.mkdir()
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        preview = client.post("/api/projects/setup-defaults", json={"path": str(root)})
        assert preview.status_code == 200
        assert preview.json() == {
            "id": "project-with-spaces",
            "name": root.name,
            "task_prefix": "PRO",
        }
        assert not (root / ".flowfield").exists()
        assert client.get("/api/projects").json() == []
        chosen = {"id": "custom", "name": "My project", "task_prefix": "CUS"}
        assert (
            client.post("/api/projects/initialize", json={"path": str(root), **chosen}).status_code
            == 200
        )
        assert (
            client.post("/api/projects/setup-defaults", json={"path": str(root)}).json() == chosen
        )
        assert (
            client.post(
                "/api/projects/setup-defaults", json={"path": str(root / "missing")}
            ).status_code
            == 400
        )
    # A portable config is also honored before registration in another workspace.
    with TestClient(create_app(data_dir=tmp_path / "fresh"), base_url="http://localhost") as client:
        assert (
            client.post("/api/projects/setup-defaults", json={"path": str(root)}).json() == chosen
        )
        assert client.get("/api/projects").json() == []
