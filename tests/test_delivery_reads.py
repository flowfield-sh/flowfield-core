"""Delivery and concurrent ordinary reads stay coherent; no models or personal state."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from result_fixtures import approve, current, fixture

from flowfield.api import create_app


def test_concurrent_delivery_board_result_and_preview_reads(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    approve(service, version)
    app = create_app()
    app.state.workspace, app.state.supervisor = service.workspace, service
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    paths = [
        "/api/workspaces/harbor/view/board",
        "/api/workspaces/harbor/view/attention?column=action",
        f"/api/workspaces/harbor/results/{version.id}",
        f"/api/workspaces/harbor/inspection?result_id={version.id}",
        f"/api/workspaces/harbor/integrations/{version.integration_id}",
    ]

    def reads():
        for _ in range(4):
            for path in paths:
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(reads),
            executor.submit(reads),
            executor.submit(service.results.process, "harbor"),
        ]
        for future in futures:
            future.result(timeout=20)
    assert current(service).status == "delivered"
