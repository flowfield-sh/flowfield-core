from pathlib import Path

from fastapi.testclient import TestClient

from flowfield import __version__
from flowfield.api import create_app


def test_health_and_missing_ui(tmp_path: Path) -> None:
    with TestClient(
        create_app(web_dir=tmp_path, data_dir=tmp_path / "state"), base_url="http://127.0.0.1"
    ) as client:
        assert client.get("/api/health").json() == {"status": "ok", "version": __version__}
        assert client.get("/").status_code == 503
        assert client.get("/api/unknown").status_code == 404


def test_built_ui_and_api_coexist(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Flowfield</h1>")
    (tmp_path / "asset.js").write_text("/* bundled */")
    with TestClient(
        create_app(web_dir=tmp_path, data_dir=tmp_path / "state"), base_url="http://localhost"
    ) as client:
        assert client.get("/").text == "<h1>Flowfield</h1>"
        assert client.get("/asset.js").status_code == 200
        assert client.get("/projects/harbor/tasks/HAR-1/activity").text == "<h1>Flowfield</h1>"
        assert client.get("/projects/harbor/settings").text == "<h1>Flowfield</h1>"
        assert client.get("/settings/harnesses").text == "<h1>Flowfield</h1>"
        assert client.get("/settings/appearance").text == "<h1>Flowfield</h1>"
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/unknown").json() == {"detail": "Not Found"}
        assert client.get("/missing.js").status_code == 404


def test_host_and_origin_boundaries(tmp_path: Path) -> None:
    with TestClient(
        create_app(web_dir=tmp_path, data_dir=tmp_path / "state"), base_url="http://localhost"
    ) as client:
        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
        assert (
            client.get("/api/health", headers={"origin": "https://evil.example"}).status_code == 403
        )
        assert client.get("/api/health", headers={"origin": "http://localhost"}).status_code == 200
