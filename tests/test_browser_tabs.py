"""Saved navigation checks presence, not actionability or archived/deleted state."""

from fastapi.testclient import TestClient
from workspace_fixtures import TEST_AGENTS

from flowfield.api import create_app


def test_resource_presence_is_scoped_bounded_and_preserves_history(tmp_path):
    with TestClient(create_app(data_dir=tmp_path), base_url="http://localhost") as client:
        space = client.post(
            "/api/workspaces",
            json={
                "name": "Saved tabs",
                **{role: choice.model_dump() for role, choice in TEST_AGENTS.items()},
            },
        ).json()["id"]
        base = f"/workspaces/{space}"
        task = client.post(f"/api{base}/tasks", json={"title": "Retained task"}).json()
        doc = client.post(
            f"/api{base}/documents", json={"title": "Retained document", "body": "Exact text"}
        ).json()
        deleted = client.request(
            "DELETE",
            f"/api{base}/documents/{doc['id']}",
            json={"expected_revision": doc["revision"]},
        )
        assert deleted.status_code == 200
        available = [
            base + "/inbox",
            base + "/tasks/T-1/settings",
            base + f"/tasks/{task['id']}",
            base + f"/documents/{doc['id']}",
            base + f"/documents/{doc['id']}/revisions/1",
        ]
        missing = [
            base + "/tasks/T-404",
            base + "/documents/missing/edit",
            base + f"/documents/{doc['id']}/revisions/404",
            base + "/results/missing",
            base + "/requests/question/missing",
            "/projects/missing/overview",
            "/workspaces/elsewhere/tasks/T-1",
        ]
        endpoint = f"/api{base}/view/resource-presence"
        response = client.get(endpoint, params=[("location", url) for url in available + missing])
        assert response.status_code == 200
        assert response.json() == {
            **dict.fromkeys(available, True),
            **dict.fromkeys(missing, False),
        }
        assert client.get(endpoint, params=[("location", base)] * 25).status_code == 422
        assert client.get(endpoint, params={"location": "x" * 4097}).status_code == 422
