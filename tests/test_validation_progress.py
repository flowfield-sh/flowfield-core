"""Read actual command output before completion, without changing durable bindings."""

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from result_fixtures import current, fixture

from flowfield.api import create_app
from flowfield.execution_models import RunAction


def wait_for(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Validation did not reach the expected phase")


def live(service):
    version = current(service)
    if not version.integration_id:
        return None
    record = service.integrations.read("harbor", version.integration_id)
    return record if record.progress and record.progress.current else None


def test_incremental_validation_read_keeps_exact_identity_and_final_evidence(tmp_path):
    service, _, _ = fixture(
        tmp_path, checks=["printf 'first output'; sleep 2; printf ' last output'", "exit 2"]
    )
    notifications = []
    service.workspace.on_activity = lambda *args: notifications.append(args)
    app = create_app()
    app.state.workspace, app.state.supervisor = service.workspace, service
    client = TestClient(app, base_url="http://127.0.0.1")
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(service.results.process, "harbor")
        record = wait_for(
            lambda: (
                (value := live(service))
                and "first output" in value.progress.current.output
                and value
            )
        )
        assert not pending.done()
        assert record.status == "preparing" and record.progress.phase == "checks"
        assert record.progress.current.index == 1 and record.progress.current.total == 2
        assert record.progress.current.last_output_at
        response = client.get(f"/api/workspaces/harbor/integrations/{record.id}")
        assert response.status_code == 200
        assert "first output" in response.json()["progress"]["current"]["output"]
        revision = current(service).revision
        wait_for(lambda: (value := live(service)) and value.progress.current.elapsed_seconds >= 0.5)
        assert current(service).revision == revision
        assert service.integrations.get("harbor", record.id).checks == []
        pending.result(timeout=10)
    final = service.integrations.read("harbor", record.id)
    assert final.progress is None and final.status == "failed"
    assert [c.exit_code for c in final.checks] == [0, 2]
    assert final.checks[0].output == "first output last output"
    assert notifications and len(notifications) < 15
    assert not service.workspace.validation_progress


def test_stop_checks_cleans_process_and_preserves_output(tmp_path):
    service, _, _ = fixture(
        tmp_path, checks=["printf 'before stop'; sleep 30; printf 'must not run'"]
    )
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(service.results.process, "harbor")
        record = wait_for(
            lambda: (
                (value := live(service))
                and "before stop" in value.progress.current.output
                and value
            )
        )
        version = current(service)
        service.results.cancel("harbor", version.id, RunAction(expected_revision=version.revision))
        pending.result(timeout=5)
    final = service.integrations.read("harbor", record.id)
    assert current(service).status == "cancelled"
    assert final.progress is None
    assert final.problem_code == "validation_cancelled"
    assert final.checks[0].exit_code == 130
    assert final.checks[0].output == "before stop"


def test_validation_timeout_bounds_recent_output_and_restart_read(tmp_path):
    from flowfield.application import Workspace
    from flowfield.integration import Integrations
    from flowfield.integration_models import IntegrationConfig
    from flowfield.storage import acquire_lock

    service, _, _ = fixture(
        tmp_path, checks=["head -c 25000 /dev/zero | tr '\\0' x; printf tail; sleep 30"]
    )
    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch=settings.target_branch,
            checks=settings.checks,
            check_timeout_seconds=2,
        ),
    )
    # Match a running service: independent readers share initialization while
    # its execution lease prevents them from becoming a second initializer.
    with (
        acquire_lock(service.workspace.directory, ".execution.lock"),
        ThreadPoolExecutor() as executor,
    ):
        pending = executor.submit(service.results.process, "harbor")
        record = wait_for(
            lambda: (
                (value := live(service))
                and value.progress.current.output.endswith("tail")
                and value
            )
        )
        assert record.progress.current.truncated
        assert len(record.progress.current.output.encode()) <= 12000
        restarted_read = Integrations(Workspace(service.workspace.directory)).read(
            "harbor", record.id
        )
        assert restarted_read.progress is None  # Another service cannot claim this process is live.
        pending.result(timeout=8)
    final = service.integrations.read("harbor", record.id)
    assert final.checks[0].exit_code == 124
    assert final.checks[0].truncated and len(final.checks[0].output.encode()) == 12000
    assert not service.workspace.validation_progress
