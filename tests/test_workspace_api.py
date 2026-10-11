"""Explicit workspace entry works before projects or native model calls exist."""

from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from flowfield.api import create_app
from flowfield.cli import app
from flowfield.execution_models import ModelOption


def test_explicit_creation_adoption_scope_and_no_implicit_resources(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Entry must not discover or start a native harness")

    monkeypatch.setattr("flowfield.catalogs.model_options", unexpected)
    state = tmp_path / "state"
    with TestClient(create_app(data_dir=state), base_url="http://localhost") as client:
        assert client.get("/api/workspaces").json() == []
        assert client.get("/api/projects").json() == []
        denied = client.post(
            "/api/workspaces",
            json={"name": "Foreign"},
            headers={"origin": "https://elsewhere.example"},
        )
        assert denied.status_code == 403
        created = client.post("/api/workspaces", json={"name": "Product"})
        assert created.status_code == 201
        space = created.json()["id"]
        base = f"/api/workspaces/{space}"
        assert client.get(base + "/projects").json() == []
        milestone = client.post(
            base + "/milestones", json={"title": "Launch", "body": "Shared intent"}
        )
        assert milestone.status_code == 201
        assert milestone.json()["workspace_id"] == space
        assert client.get(base + "/milestones/M-1/text").json()["text"] == "Shared intent"
        changed = client.put(
            base + "/milestones/M-1", json={"expected_revision": 1, "title": "Launch together"}
        )
        assert changed.status_code == 200
        assert client.get(base + "/milestones").json() == [changed.json()]
        assert client.get(base + "/milestones/M-1/text?revision=1").status_code == 409
        assert client.get(base + "/coordinator-settings").json()["selection"] is None
        assert client.get(base + "/workers").json()["enabled"] is False
        assert client.get(base + "/workers/occupancy").json()["active"] == 0
        assert not (state / "coordinators").exists()
        # Failed adoption creates neither a directory nor a project.
        assert (
            client.post(base + "/projects", json={"path": str(tmp_path / "missing")}).status_code
            == 400
        )
        assert not (tmp_path / "missing").exists()
        for name in ("core", "site"):
            root = tmp_path / name
            root.mkdir()
            (root / "AGENTS.md").write_text("Existing project conventions")
            response = client.post(base + "/projects", json={"path": str(root)})
            assert response.status_code == 201, response.text
            assert not (root / ".git").exists()
            assert (root / "AGENTS.md").read_text() == "Existing project conventions"
        assert {p["id"] for p in client.get(base + "/projects").json()} == {"core", "site"}
        second = client.post("/api/workspaces", json={"name": "Other"}).json()["id"]
        stolen = client.post(
            f"/api/workspaces/{second}/projects", json={"path": str(tmp_path / "core")}
        )
        assert stolen.status_code == 409
        assert client.get(f"/api/workspaces/{second}/projects").json() == []
        assert client.put(base, json={"expected_revision": 1, "name": "Renamed"}).status_code == 200
        assert client.put(base, json={"expected_revision": 1, "name": "Stale"}).status_code == 409
        assert client.get(base).json()["name"] == "Renamed"
        assert client.get("/api/workspaces/missing/projects").status_code == 404
        assert not (state / "coordinators").exists()


def test_shared_role_settings_validate_in_managed_home_and_preserve_queue(tmp_path, monkeypatch):
    calls = []

    async def discover(directory, *, cwd, registration, on_cleanup, **kwargs):
        calls.append(cwd)
        on_cleanup(True)
        return [ModelOption(id="fixture", name="Fixture", efforts=["low"])]

    monkeypatch.setattr("flowfield.catalogs.model_options", discover)
    state = tmp_path / "state"
    with TestClient(create_app(data_dir=state), base_url="http://localhost") as client:
        space = client.post("/api/workspaces", json={"name": "Product"}).json()["id"]
        base = f"/api/workspaces/{space}"
        assert client.get(base + "/models").status_code == 200
        assert calls == [Path(state / "coordinators" / space)]
        choice = {"model": "fixture", "effort": "low"}
        for role in ("coordinator-settings", "workers"):
            bad = client.put(
                base + "/" + role, json={"expected_revision": 1, "selection": {"model": "missing"}}
            )
            assert bad.status_code == 409
            assert client.get(base + "/" + role).json()["revision"] == 1
            saved = client.put(
                base + "/" + role, json={"expected_revision": 1, "selection": choice}
            )
            assert saved.status_code == 200, saved.text
        assert len(calls) == 1
        assert client.get(base + "/workers").json()["enabled"] is False
        assert (
            client.post(base + "/queue", json={"expected_revision": 2, "enabled": True}).status_code
            == 200
        )
        repo = tmp_path / "core"
        repo.mkdir()
        assert (
            client.post(
                base + "/projects", json={"path": str(repo), "coordinator": choice}
            ).status_code
            == 422
        )
        assert client.post(base + "/projects", json={"path": str(repo)}).status_code == 201
        assert client.get("/api/projects/core/workers").json()["enabled"] is True
        assert (
            client.get("/api/projects/core/coordinator-settings").json()["effective"]["source"]
            == "workspace"
        )
        # A stale save fails before optional native discovery is restarted.
        client.app.state.supervisor.catalogs.cache.clear()
        assert (
            client.put(
                base + "/workers", json={"expected_revision": 1, "selection": choice}
            ).status_code
            == 409
        )
        assert len(calls) == 1


def test_cli_adoption_requires_explicit_workspace_without_contacting_service(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Missing workspace cannot contact the service")

    monkeypatch.setattr("flowfield.client.Client.request", unexpected)
    result = CliRunner().invoke(app, ["project", "init", "."])
    assert result.exit_code == 2 and "--workspace" in result.output
