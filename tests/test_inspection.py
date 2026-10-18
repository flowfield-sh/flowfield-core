"""Real Git inspection and delivery isolation; no models or automatic preview commands."""

import json
import os
import select
import shlex
import shutil
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from result_fixtures import approve, current, fixture
from result_recovery_fixtures import target_change

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.application import TaskEdit, TaskReconcile
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, WorkerResult
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare
from flowfield.integration_models import IntegrationConfig
from flowfield.result_models import ResultReview


def prepare(inspections, version, **kwargs):
    return inspections.prepare(
        "harbor",
        InspectionPrepare(
            result_id=version.id,
            expected_revision=version.revision,
            **kwargs,
        ),
    )


def test_combined_candidate_dirty_preview_and_current_destination_are_independent(tmp_path):
    service, repo, run = fixture(tmp_path)
    target_change(service, repo, run.base_commit)
    (repo / "README.md").write_text("human dirty work")
    service.results.process("harbor")
    version = current(service)
    assert version.candidate_commit != run.result_commit
    inspections = Inspections(service.workspace)
    inspections.configure(
        "harbor", InspectionConfig(expected_revision=1, run_command="touch preview-ran")
    )
    copy = prepare(inspections, version)
    checkout = Path(copy.workspace)
    assert baseline(checkout) == version.candidate_commit
    assert (checkout / "target.txt").exists() and (checkout / "result.txt").exists()
    assert not (checkout / "preview-ran").exists()
    assert (
        not version.approved_at and service.workspace.task("harbor", "work").status == "in_review"
    )
    (checkout / "result.txt").write_text("human preview edit")
    (checkout / "generated").mkdir()
    (checkout / "generated/output").write_text("private preview artifact")
    repeated = prepare(Inspections(service.workspace), version)
    assert repeated.id == copy.id and repeated.locally_changed
    clean = prepare(inspections, version, new_copy=True)
    assert clean.id != copy.id and not clean.locally_changed
    assert (checkout / "result.txt").read_text() == "human preview edit"
    approve(service, version)
    inspections.configure(
        "harbor", InspectionConfig(expected_revision=2, run_command="echo updated")
    )
    service.results.process("harbor")
    assert current(service).problem_code == "checkout_dirty"
    assert (repo / "README.md").read_text() == "human dirty work"
    # The human preserves/removes the local edit before retrying approved delivery.
    (tmp_path / "preserved-readme").write_text((repo / "README.md").read_text())
    git(repo, "restore", "--worktree", "README.md")
    from flowfield.execution_models import RunAction

    service.results.retry_delivery(
        "harbor", version.id, RunAction(expected_revision=current(service).revision)
    )
    service.results.process("harbor")
    assert current(service).status == "delivered"
    assert service.integrations.head("harbor", "work") == copy.commit
    assert git(repo, "show", copy.commit + ":result.txt") == b"implemented\n"
    assert (tmp_path / "preserved-readme").read_text() == "human dirty work"


def test_feedback_successor_preserves_earlier_copy_and_approval_binding(tmp_path):
    service, repo, _ = fixture(tmp_path)
    service.results.process("harbor")
    inspections = Inspections(service.workspace)
    first = current(service)
    old = prepare(inspections, first)
    (Path(old.workspace) / "result.txt").write_text("local old feedback")
    # Revise/reconcile intent before scheduling feedback so the next claim freezes it.
    task = service.workspace.task("harbor", "work")
    task = service.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            body="Deliver the useful successor agreed after trying it.",
        ),
    )
    service.workspace.reconcile_task(
        "harbor",
        task.id,
        TaskReconcile(
            expected_revision=task.revision,
            note="The human refined the outcome after inspection; request a revised result.",
        ),
    )
    service.results.review(
        "harbor",
        first.id,
        ResultReview(
            expected_revision=first.revision,
            candidate_commit=first.candidate_commit,
            action="request_changes",
            note="Make the result useful.",
        ),
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    head = service.integrations.head("harbor", "work")
    run = service.execution.claim(
        "harbor", head, service._available("harbor", "harbor", repo, head)
    )
    assert service.execution.assignment("harbor", run.id)["description"] == task.body
    env = LocalHost(os.environ).prepare(service.workspace.directory, repo, run.id, run.base_commit)
    (env.checkout / "result.txt").write_text("useful successor")
    commit, _ = env.snapshot(run.base_commit)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        commit=commit,
        result=WorkerResult(summary="Useful", checks="Test fixture"),
    )
    service.results.process("harbor")
    successor = current(service)
    newer = prepare(inspections, successor)
    assert newer.commit == successor.candidate_commit and newer.id != old.id
    assert inspections.get("harbor", old.id).source_changed
    assert (Path(old.workspace) / "result.txt").read_text() == "local old feedback"
    with pytest.raises(ApplicationError):
        approve(service, first)
    approve(service, successor)
    service.results.process("harbor")
    assert service.integrations.head("harbor", "work") == newer.commit


def test_settings_revisions_stale_requests_and_failed_preparation_preserve_evidence(
    tmp_path, monkeypatch
):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    inspections = Inspections(service.workspace)
    saved = inspections.configure(
        "harbor", InspectionConfig(expected_revision=1, run_command="echo first")
    )
    with pytest.raises(ApplicationError):
        inspections.configure(
            "harbor", InspectionConfig(expected_revision=1, run_command="echo stale")
        )
    with pytest.raises(ApplicationError):
        inspections.prepare(
            "harbor", InspectionPrepare(result_id=version.id, expected_revision=999)
        )
    assert inspections.latest("harbor", version.id) is None
    actual = LocalHost.prepare

    def interrupted(*args):
        actual(*args)
        raise OSError("simulated crash after files")

    monkeypatch.setattr(LocalHost, "prepare", interrupted)
    failed = prepare(inspections, version)
    assert failed.status == "failed"
    retained = service.workspace.directory / "inspection-work/local-attempts" / failed.id
    assert retained.exists()
    monkeypatch.setattr(LocalHost, "prepare", actual)
    fresh = prepare(Inspections(service.workspace), version)
    assert fresh.id != failed.id and fresh.status == "ready" and retained.exists()
    # A crash before the ready receipt is durable and never repurposes the half-prepared copy.
    fresh.status = "preparing"
    inspections._save(fresh)
    recovered = prepare(inspections, version)
    assert recovered.id != fresh.id and inspections.get("harbor", fresh.id).status == "failed"
    inspections.configure(
        "harbor", InspectionConfig(expected_revision=saved.revision, run_command="echo new")
    )
    assert inspections.get("harbor", recovered.id).instructions_changed
    assert service.results.get("harbor", version.id).revision == version.revision
    original = inspections.get("harbor", recovered.id)
    assert subprocess.check_output(["/bin/sh", "-c", original.command], text=True) == "first\n"
    newest = prepare(inspections, version)
    assert subprocess.check_output(["/bin/sh", "-c", newest.command], text=True) == "new\n"
    assert newest.id != recovered.id
    Path(newest.launcher).unlink()
    assert "launcher is unavailable" in inspections.get("harbor", newest.id).problem
    assert Path(newest.workspace).exists()
    newest = prepare(inspections, version)
    Path(newest.workspace).rename(Path(newest.workspace).with_name("preserved"))
    assert inspections.get("harbor", newest.id).problem
    assert prepare(inspections, version).id != newest.id


def test_parallel_prepare_reserves_one_copy_and_wrong_project_cannot_inspect(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    inspections = Inspections(service.workspace)
    version = current(service)

    def request(_):
        try:
            return prepare(inspections, version).id
        except ApplicationError as error:
            assert error.code == "integration_busy"
            return None

    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(request, range(2)))
    assert len(set(i for i in ids if i)) == 1
    assert prepare(inspections, version).id in ids
    with pytest.raises(ApplicationError):
        inspections.get("foreign", next(i for i in ids if i))


def test_node_setup_and_run_artifacts_stay_in_inspection_copy(tmp_path):
    npm = shutil.which("npm")
    if not npm:
        pytest.skip("Node/npm toolchain needed for the explicit Node inspection test")
    service, repo, original = fixture(tmp_path, checks=["test -f package.json"])
    env = LocalHost(os.environ).prepare(
        service.workspace.directory, repo, "node-source", original.result_commit
    )
    (env.checkout / ".gitignore").write_text("node_modules/\noutput.txt\n")
    vendor = env.checkout / "vendor/greeting"
    vendor.mkdir(parents=True)
    (vendor / "package.json").write_text(
        json.dumps({"name": "greeting", "version": "1.0.0", "main": "index.js"})
    )
    (vendor / "index.js").write_text("module.exports = 'candidate works';\n")
    (env.checkout / "package.json").write_text(
        json.dumps(
            {
                "name": "inspection-toy",
                "version": "1.0.0",
                "private": True,
                "dependencies": {"greeting": "file:vendor/greeting"},
                "scripts": {"start": "node app.cjs"},
            }
        )
    )
    (env.checkout / "app.cjs").write_text(
        "require('fs').writeFileSync('output.txt', require('greeting'));\n"
    )
    subprocess.run(
        [
            npm,
            "install",
            "--package-lock-only",
            "--offline",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=env.checkout,
        check=True,
        capture_output=True,
        env={**os.environ, "npm_config_cache": str(tmp_path / "fixture-cache")},
        timeout=30,
    )
    commit, _ = env.snapshot(original.result_commit)
    # This deterministic submitted source is fixture construction, not a worker claim.
    with service.workspace.connection(write=True) as db:
        run = service.execution._run(db, "harbor", original.id)
        run.result_commit = commit
        service.execution._save(db, run)
    integration = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=integration.revision,
            target_branch="integration",
            checks=["npm start"],
            setup_commands=["npm ci --offline --ignore-scripts --no-audit --no-fund"],
        ),
    )
    # Local validation runs only the configured check; inspection installs its own dependencies.
    # Preparation tests real npm independently; model/worker Node capability is a later live layer.
    service.results.process("harbor")
    version = current(service)
    assert version.candidate_commit and version.status == "ready", version.problem
    inspections = Inspections(service.workspace)
    inspections.configure("harbor", InspectionConfig(expected_revision=1, run_command="npm start"))
    copy = prepare(inspections, version)
    checkout = Path(copy.workspace)
    assert not (checkout / "node_modules").exists() and not (checkout / "output.txt").exists()
    subprocess.run(["/bin/sh", "-c", copy.command], check=True, capture_output=True, timeout=30)
    assert (checkout / "output.txt").read_text() == "candidate works"
    assert (checkout / "node_modules/greeting").resolve().is_relative_to(checkout)
    assert not (env.checkout / "node_modules").exists() and not (repo / "output.txt").exists()
    assert not inspections.get("harbor", copy.id).locally_changed  # Only ignored generated files.
    assert git(repo, "show", copy.commit + ":app.cjs")


def test_real_mcp_and_http_inspect_the_same_candidate_without_running_commands(tmp_path):
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from service_fixtures import running_service

    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)

    async def exercise(base):
        async with (
            streamable_http_client(base + "/mcp/") as (read, write, _),
            ClientSession(read, write) as session,
            httpx.AsyncClient(base_url=base, trust_env=False) as api,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {
                "prepare_inspection",
                "get_inspection",
                "configure_inspection",
                "get_inspection_settings",
            } <= names
            assert (
                await api.get("/api/workspaces/harbor/inspection", params={"result_id": version.id})
            ).json() is None
            assert (await api.get("/api/workspaces/harbor/inspection")).status_code == 422
            missing_source = await session.call_tool("get_inspection", {"workspace_id": "harbor"})
            assert missing_source.isError
            rejected = await api.post(
                "/api/workspaces/harbor/inspection", json={"expected_revision": 1}
            )
            assert rejected.status_code == 422
            rejected_mcp = await session.call_tool(
                "prepare_inspection",
                {"workspace_id": "harbor", "request": {"expected_revision": 1}},
            )
            assert rejected_mcp.isError
            result = await session.call_tool(
                "configure_inspection",
                {
                    "project_id": "harbor",
                    "settings": {"expected_revision": 1, "run_command": "touch not-automatic"},
                },
            )
            assert not result.isError
            result = await session.call_tool(
                "prepare_inspection",
                {
                    "workspace_id": "harbor",
                    "request": {"result_id": version.id, "expected_revision": version.revision},
                },
            )
            assert not result.isError, result
            copy = result.structuredContent
            assert copy["commit"] == version.candidate_commit
            assert not (Path(copy["workspace"]) / "not-automatic").exists()
            saved = (await api.get(f"/api/workspaces/harbor/inspections/{copy['id']}")).json()
            assert saved["commit"] == copy["commit"] and saved["command"] == copy["command"]
            # Long trusted setup stays in the saved launcher, outside the short command.
            settings = (await api.get("/api/projects/harbor/integration")).json()
            configured = await api.put(
                "/api/projects/harbor/integration",
                json={
                    "expected_revision": settings["revision"],
                    "target_branch": "integration",
                    "checks": settings["checks"],
                    "setup_commands": ["#" + "x" * 3900] * 3,
                },
            )
            assert configured.status_code == 200
            # Settings invalidate the result asynchronously; observe that transition before
            # using its current revision, rather than racing the background validator.
            with anyio.fail_after(10):
                while True:
                    selected = (
                        await api.get(f"/api/workspaces/harbor/results/{version.id}")
                    ).json()
                    if selected["status"] == "stale":
                        break
                    await anyio.sleep(0.05)
            prepared = await session.call_tool(
                "prepare_inspection",
                {
                    "workspace_id": "harbor",
                    "request": {
                        "result_id": version.id,
                        "expected_revision": selected["revision"],
                    },
                },
            )
            assert not prepared.isError
            item = prepared.structuredContent
            assert item["command_next_offset"] is None
            assert len(item["command"]) < 1000
            full = (await api.get(f"/api/workspaces/harbor/inspections/{item['id']}")).json()
            assert item["command"] == full["command"]
            assert len(Path(full["launcher"]).read_text()) > 8000
            stale = await api.post(
                "/api/workspaces/harbor/inspection",
                json={"result_id": version.id, "expected_revision": 999},
            )
            assert stale.status_code == 409

    with running_service(service.workspace.directory) as base:
        anyio.run(exercise, base)


def test_report_has_no_try_candidate(tmp_path):
    service, _, _ = fixture(tmp_path, completion="report", changed=False)
    service.results.process("harbor")
    with pytest.raises(ApplicationError, match="no prepared code candidate"):
        prepare(Inspections(service.workspace), current(service))


def test_saved_launcher_quotes_paths_and_environment_and_preserves_setup_failure(tmp_path):
    directory = tmp_path / "copy with ' quotes $(literal)"
    directory.mkdir()
    service, repo, _ = fixture(directory)
    settings = service.integrations.settings("harbor")
    value = "a 'quoted' value; $(touch injected)"
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch=settings.target_branch,
            checks=settings.checks,
            setup_commands=[f"printf %s {shlex.quote(value)} > setup-output"],
        ),
    )
    service.results.process("harbor")
    inspections = Inspections(service.workspace)
    inspections.configure(
        "harbor", InspectionConfig(expected_revision=1, run_command="cat setup-output")
    )
    copy = prepare(inspections, current(service))
    checkout, launcher = Path(copy.workspace), Path(copy.launcher)
    assert not (checkout / "setup-output").exists()
    assert not launcher.is_relative_to(checkout)
    assert launcher.stat().st_mode & 0o777 == 0o600
    assert shlex.split(copy.command) == ["/bin/sh", str(launcher)]
    assert subprocess.check_output(["/bin/sh", "-c", copy.command], text=True) == value
    assert not (checkout / "injected").exists() and not (repo / "setup-output").exists()
    saved = launcher.read_bytes()
    assert prepare(inspections, current(service)).id == copy.id
    assert launcher.read_bytes() == saved

    settings = service.integrations.settings("harbor")
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=settings.revision,
            target_branch=settings.target_branch,
            checks=settings.checks,
            setup_commands=["exit 23"],
        ),
    )
    inspections.configure(
        "harbor", InspectionConfig(expected_revision=2, run_command="touch should-not-run")
    )
    failed = prepare(inspections, current(service))
    result = subprocess.run(["/bin/sh", "-c", failed.command], timeout=10)
    assert result.returncode == 23
    assert not (Path(failed.workspace) / "should-not-run").exists()
    assert subprocess.check_output(["/bin/sh", "-c", copy.command], text=True) == value
    assert inspections.get("harbor", copy.id).instructions_changed


def test_launcher_stops_with_its_terminal_process_group(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    inspections = Inspections(service.workspace)
    # Block before announcing readiness so the group interrupt stays pending even
    # if it arrives before the child enters its wait. signal.pause has no such handoff.
    inspections.configure(
        "harbor",
        InspectionConfig(
            expected_revision=1,
            run_command="trap 'exit 130' INT\n"
            "python -c 'import signal; "
            "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT}); "
            'print("ready", flush=True); '
            "signal.sigwait({signal.SIGINT}); raise SystemExit(130)'\n"
            "touch survived",
        ),
    )
    copy = prepare(inspections, current(service))
    process = subprocess.Popen(
        shlex.split(copy.command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline() == b"ready\n"
        # A terminal sends Ctrl+C to its foreground process group, including children.
        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=5)
        assert process.returncode == 130
        assert not (Path(copy.workspace) / "survived").exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
