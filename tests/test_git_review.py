import os
from pathlib import Path

import pytest

from flowfield.adapters.git_review import changed_files, file_patch
from flowfield.adapters.git_workspace import baseline, git
from flowfield.errors import ApplicationError


def commit(repo: Path) -> str:
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return baseline(repo)


def test_complete_file_pages_and_immutable_patch(tmp_path: Path) -> None:
    git(tmp_path, "init")
    (tmp_path / "old name.txt").write_text("unchanged\n" * 20)
    (tmp_path / "delete.txt").write_text("remove\n")
    (tmp_path / "code.py").write_text("value = 1\n")
    base = commit(tmp_path)
    (tmp_path / "old name.txt").rename(tmp_path / "new name.txt")
    (tmp_path / "delete.txt").unlink()
    (tmp_path / "code.py").write_text("value = 2\n")
    (tmp_path / "odd\n☃.txt").write_text("a new file\n")
    (tmp_path / "image.bin").write_bytes(b"\0binary")
    (tmp_path / "large.txt").write_text("large\n" * 50_000)
    (tmp_path / "many-lines.txt").write_text("x\n" * 5000)
    result = commit(tmp_path)
    # Working-tree changes must never alter the review of captured commits.
    (tmp_path / "code.py").write_text("unreviewed working copy\n")
    first = changed_files(tmp_path, base, result, 0, 2)
    assert first.total_files == 7 and first.next_offset == 2
    rest = changed_files(tmp_path, base, result, 2, 100)
    files = first.files + rest.files
    assert rest.next_offset is None
    assert len({f.id for f in files}) == 7
    patches = {f.new_path or f.old_path: file_patch(tmp_path, base, result, f.id) for f in files}
    code = patches["code.py"]
    assert code.text and "-value = 1\n+value = 2\n" in code.text
    assert "working copy" not in code.text
    assert code.base_commit == base and code.result_commit == result
    assert patches["delete.txt"].file.change == "deleted"
    assert patches["new name.txt"].file.change == "renamed"
    assert patches["odd\n☃.txt"].text
    assert patches["image.bin"].binary and patches["image.bin"].text is None
    assert "256 KB" in patches["large.txt"].omitted_reason
    assert patches["many-lines.txt"].text is None
    assert "4,000 lines" in patches["many-lines.txt"].omitted_reason
    with pytest.raises(ApplicationError, match="file"):
        file_patch(tmp_path, base, result, 99)


def test_empty_and_mode_only_changes_and_literal_paths(tmp_path: Path) -> None:
    git(tmp_path, "init")
    (tmp_path / "[abc].txt").write_text("before\n")
    (tmp_path / "a.txt").write_text("unrelated\n")
    base = commit(tmp_path)
    assert changed_files(tmp_path, base, base, 0, 50).files == []
    (tmp_path / "[abc].txt").write_text("after\n")
    (tmp_path / "a.txt").chmod(0o755)
    result = commit(tmp_path)
    files = changed_files(tmp_path, base, result, 0, 50).files
    literal = next(f for f in files if f.new_path == "[abc].txt")
    patch = file_patch(tmp_path, base, result, literal.id)
    assert patch.text.count("diff --git") == 1
    mode = file_patch(tmp_path, base, result, next(f.id for f in files if f.new_path == "a.txt"))
    assert "new mode 100755" in mode.text and "@@" not in mode.text


def test_review_api_files_and_successor_use_captured_commits(tmp_path: Path, monkeypatch) -> None:
    """Actual HTTP review -> revised result -> acceptance, with no harness process."""

    from execution_fixtures import fixture
    from fastapi.testclient import TestClient

    from flowfield.adapters.local_execution import LocalHost
    from flowfield.api import create_app
    from flowfield.application import TaskPublish
    from flowfield.execution_models import QueueEdit, WorkerResult
    from flowfield.integration import Integrations
    from flowfield.integration_models import IntegrationConfig
    from flowfield.results import Results
    from flowfield.supervisor import Supervisor

    async def no_scheduler(self):
        pass

    monkeypatch.setattr(Supervisor, "_schedule", no_scheduler)
    execution = fixture(tmp_path)
    repo = tmp_path / "harbor"
    git(repo, "init")
    (repo / "loader.py").write_text("value = 1\n")
    base = commit(repo)
    Integrations(execution.workspace).configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="HEAD",
            checks=["test -f loader.py"],
        ),
    )
    task = execution.workspace.task("harbor", "task-0")
    execution.workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            completion="code",
            expected_revision=task.revision,
        ),
    )

    def finish_attempt():
        settings = execution.settings("harbor")
        execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
        run = execution.claim("harbor", base, {base: set()})
        assert run
        env = LocalHost(os.environ).prepare(tmp_path / "state", repo, run.id, run.base_commit)
        execution.save_local(
            run.id,
            {
                "runtime_kind": "local",
                **{
                    key: str(getattr(env, key))
                    for key in ("root", "checkout", "runtime", "common_git")
                },
            },
        )
        execution.started("harbor", run.id)
        (env.checkout / "loader.py").write_text(
            "value = 2\n" if not run.predecessor_id else "value = 3\n"
        )
        result, _ = env.snapshot(run.base_commit)
        return execution.finish(
            "harbor",
            run.id,
            "in_review",
            result=WorkerResult(
                summary="Deterministic fixture result", checks="Fixture checks only"
            ),
            commit=result,
        )

    first = finish_attempt()
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        path = f"/api/workspaces/harbor/runs/{first.id}"
        listing = client.get(path + "/diff").json()
        assert listing["total_files"] == 1
        assert client.get(path + "/diff?offset=-1").status_code == 422
        assert client.get(path + "/diff?limit=101").status_code == 422
        assert client.get(path + "/diff/0").json()["text"].endswith("+value = 2\n")
        assert client.get(path.replace("harbor", "other") + "/diff").status_code == 404
        stale = client.post(
            path + "/review",
            json={
                "expected_revision": first.revision,
                "action": "accept",
                "result_commit": base,
            },
        )
        assert stale.status_code == 404
        version = client.get("/api/workspaces/harbor/tasks/task-0/results").json()["items"][0]
        requested = client.post(
            f"/api/workspaces/harbor/results/{version['id']}/review",
            json={
                "expected_revision": version["revision"],
                "action": "request_changes",
                "candidate_commit": version["candidate_commit"] or version["source_commit"],
                "note": "Use value 3.",
            },
        )
        assert requested.status_code == 200
        successor = finish_attempt()
        assert successor.predecessor_id == first.id and successor.base_commit == first.result_commit
        next_path = f"/api/workspaces/harbor/runs/{successor.id}"
        patch = client.get(next_path + "/diff/0").json()
        assert "-value = 2\n+value = 3\n" in patch["text"]
        Results(execution.workspace).process("harbor")
        version = client.get("/api/workspaces/harbor/tasks/task-0/results").json()["items"][0]
        accepted = client.post(
            f"/api/workspaces/harbor/results/{version['id']}/review",
            json={
                "expected_revision": version["revision"],
                "candidate_commit": version["candidate_commit"],
                "action": "approve",
            },
        )
        assert accepted.json()["status"] == "delivering"
        assert execution.workspace.task("harbor", "task-0").status == "in_review"
        assert client.get("/api/workspaces/harbor/runs?attention=true").json()["items"] == []
