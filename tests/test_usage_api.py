"""Typed local read routes preserve workspace isolation and reject oversized queries."""

from fastapi.testclient import TestClient
from usage_fixtures import accounting, event
from workspace_fixtures import TEST_AGENTS

from flowfield.api import create_app
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_usage_http_contract_scope_validation_and_origin(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run))
    other = Workspaces(execution.workspace).create(WorkspaceCreate(name="Other", **TEST_AGENTS))
    app = create_app(data_dir=execution.workspace.directory)
    # Read routes use the same existing Workspace owner; no scheduler or native work.
    app.state.workspace = execution.workspace
    with TestClient(app, base_url="http://localhost") as client:
        report = client.get(f"/api/workspaces/harbor/tasks/{run.task_id}/usage")
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["totals"]["tokens"]["total"] == 180
        assert body["contracts"][0]["native_version"] == "2.1.293"
        evidence = client.get(f"/api/workspaces/harbor/usage/run/{run.id}")
        assert evidence.status_code == 200
        assert evidence.json()["items"][0]["delta"]["reasoning"] == 10
        assert client.get(f"/api/workspaces/{other.id}/usage/run/{run.id}").status_code == 404
        assert (
            client.get(f"/api/workspaces/{other.id}/tasks/{run.task_id}/usage").status_code == 404
        )
        assert client.get("/api/workspaces/harbor/usage?limit=101").status_code == 422
        assert client.get("/api/workspaces/harbor/usage?timezone=Invalid").status_code == 422
        assert (
            client.get(
                "/api/workspaces/harbor/usage", headers={"Origin": "https://foreign.invalid"}
            ).status_code
            == 403
        )
        assert client.get("/api/workspaces/harbor/usage").headers["cache-control"] == "no-store"
