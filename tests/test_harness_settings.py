"""Host registration, frozen launches and supported-state upgrades; no live models."""

import asyncio
import json
import sys
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError
from test_coordinator import message, settled, setup
from test_execution import BASE, fixture
from typer.testing import CliRunner

from flowfield import migrations, storage
from flowfield.adapters import harness_host
from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.coordinator_sessions import CoordinatorSessions, NativeGeneration
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.api import create_app
from flowfield.application import Workspace, now
from flowfield.cli import app as cli
from flowfield.client import Client
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.harness_models import CatalogOwnership, HarnessEdit, HarnessRegistration
from flowfield.harness_settings import HarnessSettings, offline_registration


def native(tmp_path, harness="claude-code", *, logged_in=True):
    path = tmp_path / "native harness"
    log = tmp_path / "native-calls.jsonl"
    login_text = "Logged in using ChatGPT" if logged_in else "Not logged in"
    auth = {
        "loggedIn": logged_in,
        "email": "native-private-value",
        "apiKey": "native-private-value",
    }
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print({'codex-cli 0.155.1' if harness == 'codex' else '2.1.295 (Claude Code)'!r})\n"
        "else:\n"
        + (
            f"    print({login_text!r}, file=sys.stderr)\n    sys.exit({0 if logged_in else 1})\n"
            if harness == "codex"
            else f"    print(json.dumps({auth!r}))\n"
        )
    )
    path.chmod(0o755)
    return path, log


def test_one_registration_per_kind_conflicts_reset_and_no_native_writes(tmp_path):
    workspace = Workspace(tmp_path / "state")
    settings = HarnessSettings(workspace)
    initial = settings.all()
    assert [item.harness for item in initial] == ["codex", "claude-code", "pi"]
    missing = tmp_path / "not-created"
    saved = settings.edit(
        "claude-code", HarnessEdit(expected_revision=1, config_directory=str(missing))
    )
    assert saved.revision == 2 and not missing.exists()
    with pytest.raises(ApplicationError, match="stale"):
        settings.edit("claude-code", HarnessEdit(expected_revision=1))
    reset = settings.edit("claude-code", HarnessEdit(expected_revision=2))
    assert reset.revision == 3 and reset.executable is None and reset.config_directory is None
    assert settings.get("codex") == initial[0]
    assert len(HarnessSettings(Workspace(workspace.directory)).all()) == 3
    for field in ("account", "api_key", "name", "enabled"):
        with pytest.raises(ValidationError):
            HarnessEdit(expected_revision=3, **{field: "not-owned"})
    for path in ("relative/config.toml", "~flowfield-invalid-host-user/config"):
        with pytest.raises(ValidationError):
            HarnessEdit(expected_revision=3, config_directory=path)


def test_native_precedence_resolved_launch_and_config_semantics(tmp_path):
    detected, _ = native(tmp_path)
    config = tmp_path / "configuration"
    config.mkdir()
    registration = HarnessRegistration(
        harness="claude-code", executable=str(detected), config_directory=str(config), revision=7
    )
    environment = {
        "PATH": "",
        "HOME": str(tmp_path),
        "CLAUDE_CODE_EXECUTABLE": "/missing",
        "CLAUDE_CONFIG_DIR": "/missing",
        "NATIVE_SECRET": "preserved",
    }
    launch = harness_host.resolve(registration, environment)
    assert launch.registration_revision == 7
    assert launch.executable_source == launch.config_source == "registration"
    env = harness_host.launch_environment(launch, environment)
    assert env["CLAUDE_CODE_EXECUTABLE"] == str(detected)
    assert env["CLAUDE_CONFIG_DIR"] == str(config)
    assert env["NATIVE_SECRET"] == "preserved" and environment["CLAUDE_CONFIG_DIR"] == "/missing"
    assert "preserved" not in launch.model_dump_json()
    config.rmdir()
    with pytest.raises(ApplicationError, match="existing directory"):
        harness_host.launch_environment(launch, environment)
    config.write_text("native file")
    assert not harness_host.status(tmp_path, registration, environment).config_available
    defaults = harness_host.resolve(
        HarnessRegistration(harness="codex"),
        {"HOME": str(tmp_path), "PATH": "", "CODEX_PATH": str(detected)},
    )
    assert defaults.executable_source == "environment" and defaults.config_source == "default"
    assert defaults.config_directory == str(tmp_path / ".codex")
    # Bad native env for one kind cannot prevent detecting the other kind.
    invalid = harness_host.status(
        tmp_path,
        HarnessRegistration(harness="claude-code"),
        {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": "relative"},
    )
    assert not invalid.config_available and not invalid.selectable


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("source", ["default", "environment", "registration"])
def test_native_config_environment_preserves_default_claude_account(tmp_path, harness, source):
    executable, _ = native(tmp_path, harness)
    variable = harness_host.NATIVE_PATHS[harness].config_variable
    config = tmp_path / harness_host.NATIVE_PATHS[harness].config_directory
    config.mkdir()
    environment = {"HOME": str(tmp_path), "PATH": ""}
    registration = HarnessRegistration(harness=harness, executable=str(executable))
    if source == "environment":
        environment[variable] = str(config)
    elif source == "registration":
        registration.config_directory = str(config)
    launch = harness_host.resolve(registration, environment)
    actual = harness_host.launch_environment(launch, environment)
    assert launch.config_directory == str(config) and launch.config_source == source
    if harness == "claude-code" and source == "default":
        assert variable not in actual
    else:
        assert actual[variable] == str(config)


def test_claude_default_and_explicit_config_do_not_share_native_binding(tmp_path):
    executable, _ = native(tmp_path)
    environment = {"HOME": str(tmp_path), "PATH": ""}
    registration = HarnessRegistration(harness="claude-code", executable=str(executable))
    default = harness_host.resolve(registration, environment)
    explicit = harness_host.resolve(
        registration.model_copy(update={"config_directory": default.config_directory}), environment
    )
    from_environment = harness_host.resolve(
        registration, {**environment, "CLAUDE_CONFIG_DIR": default.config_directory}
    )
    assert not harness_host.same_session_location(default, explicit)
    assert harness_host.same_session_location(explicit, from_environment)


@pytest.mark.parametrize("source", ["environment", "registration"])
def test_claude_preserves_literal_config_directory_for_native_credentials(tmp_path, source):
    executable, _ = native(tmp_path)
    real = tmp_path / "config"
    real.mkdir()
    linked = tmp_path / "linked-config"
    linked.symlink_to(real, target_is_directory=True)
    configured = str(linked) + "/"
    environment = {"HOME": str(tmp_path), "PATH": ""}
    registration = HarnessRegistration(harness="claude-code", executable=str(executable))
    if source == "environment":
        environment["CLAUDE_CONFIG_DIR"] = configured
    else:
        registration = HarnessRegistration(
            harness="claude-code", executable=str(executable), config_directory=configured
        )
    launch = harness_host.resolve(registration, environment)
    assert launch.config_directory == configured
    assert harness_host.launch_environment(launch, environment)["CLAUDE_CONFIG_DIR"] == configured
    canonical = harness_host.resolve(
        HarnessRegistration(
            harness="claude-code", executable=str(executable), config_directory=str(real)
        ),
        environment,
    )
    assert not harness_host.same_session_location(launch, canonical)


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_explicit_readiness_uses_only_bounded_native_status_and_no_sensitive_output(
    tmp_path, harness, logged_in
):
    executable, log = native(tmp_path, harness, logged_in=logged_in)
    registration = HarnessRegistration(harness=harness, executable=str(executable))
    env = {"HOME": str(tmp_path), "PATH": ""}
    detected = harness_host.status(tmp_path, registration, env)
    assert not log.exists() and detected.authentication == "unknown"
    checked = asyncio.run(harness_host.check(tmp_path, registration, env))
    assert checked.native_version == ("0.155.1" if harness == "codex" else "2.1.295")
    assert checked.authentication == ("authenticated" if logged_in else "signed-out")
    assert checked.model_access == "unverified"
    assert "native-private-value" not in checked.model_dump_json()
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        ["--version"],
        ["login", "status"] if harness == "codex" else ["auth", "status", "--json"],
    ]
    assert not (tmp_path / ".claude").exists() and not (tmp_path / ".codex").exists()


def test_native_check_failure_discards_output_and_terminates_owned_process(tmp_path):
    executable, _ = native(tmp_path)
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nprint('native-private-value' * 20000)\n"
    )
    checked = asyncio.run(
        harness_host.check(
            tmp_path,
            HarnessRegistration(harness="claude-code", executable=str(executable)),
            {"HOME": str(tmp_path), "PATH": ""},
        )
    )
    assert checked.authentication == "unknown" and "native_check_failed" in checked.problems
    assert "native-private-value" not in checked.model_dump_json()


def test_cancelled_native_check_retains_process_owner_and_drains_pipes(tmp_path, monkeypatch):
    async def exercise():
        owners = []
        original = harness_host.LocalProcess.start
        started = asyncio.Event()

        async def spawn(*args, **kwargs):
            owner = await original(*args, **kwargs)
            owners.append(owner)
            started.set()
            return owner

        monkeypatch.setattr(harness_host.LocalProcess, "start", spawn)
        task = asyncio.create_task(
            harness_host._native_command(
                [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owners[0].process.returncode is not None
        assert not owners[0]._group_exists()

    asyncio.run(exercise())


def test_checks_coalesce_remain_owned_after_request_cancel_and_reject_changed_snapshot(
    tmp_path, monkeypatch
):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def check(directory, registration, environment):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return harness_host.status(directory, registration, environment)

        monkeypatch.setattr(harness_host, "check", check)
        owner = harness_host.HarnessChecks(tmp_path)
        registration = HarnessRegistration(harness="claude-code")
        env = {"HOME": str(tmp_path), "PATH": ""}
        first = asyncio.create_task(owner.run(registration, env))
        await entered.wait()
        second = asyncio.create_task(owner.run(registration, env))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not owner.jobs["claude-code"][1].done() and calls == 1
        with pytest.raises(ApplicationError, match="changed"):
            await owner.run(registration.model_copy(update={"revision": 2}), env)
        release.set()
        assert (await second).registration.revision == 1
        await owner.run(registration, env)
        assert calls == 2  # Every explicit refresh after completion checks again.
        await owner.close()
        with pytest.raises(ApplicationError, match="stopping"):
            await owner.run(registration, env)

    asyncio.run(exercise())


def test_worker_and_coordinator_freeze_registration_before_edits(tmp_path):
    execution = fixture(tmp_path)
    workspace = execution.workspace
    settings = HarnessSettings(workspace)
    old = settings.edit(
        "codex", HarnessEdit(expected_revision=1, executable=str(tmp_path / "old-codex"))
    )
    run = execution.claim("harbor", BASE, {BASE: set()})
    AgentSettings(workspace).edit(
        "harbor",
        "coordinator",
        AgentSettingsEdit(
            expected_revision=1, selection=AgentChoice(model="native-model", effort="low")
        ),
    )
    store = CoordinatorStore(workspace)
    conversation = store.new("harbor")
    turn, _ = store.reserve(
        "harbor", conversation.id, CoordinatorSend(id="a" * 32, text="Keep this requirement")
    )
    settings.edit("codex", HarnessEdit(expected_revision=2, executable=str(tmp_path / "new-codex")))
    assert execution.get("harbor", run.id).agent_settings.registration == old
    assert store.get("harbor", turn.id).settings.registration == old


def test_codex_override_launch_is_frozen_and_saved_session_rejects_redirect(tmp_path):
    workspace = fixture(tmp_path).workspace
    executable, _ = native(tmp_path, "codex")
    config = tmp_path / "native-config"
    config.mkdir()
    registration = HarnessRegistration(
        harness="codex", executable=str(executable), config_directory=str(config), revision=2
    )
    agent = CodexAgent(
        workspace.directory,
        tmp_path,
        {"PATH": "", "HOME": str(tmp_path)},
        registration=registration,
    )
    assert agent.environment["CODEX_PATH"] == str(executable) and agent.environment[
        "CODEX_HOME"
    ] == str(config)
    assert (
        agent.launch.registration_revision == 2
        and agent.launch.adapter_version == "codex-app-server-v1"
    )
    with workspace.connection(write=True) as db:
        generation = NativeGeneration(
            id="a" * 32,
            project_id="harbor",
            created_at=now(),
            harness="codex",
            session_id="native-id",
            cwd=str(tmp_path),
            launch=agent.launch,
            status="retained",
        )
        CoordinatorSessions.save(db, generation)
        db.execute(
            "INSERT INTO coordinator_sessions VALUES (?,?,0)",
            ("harbor", generation.id),
        )
    store = CoordinatorStore(workspace)
    assert store.session("harbor", "codex", str(tmp_path), launch=agent.launch) == "native-id"
    assert (
        store.session(
            "harbor",
            "codex",
            str(tmp_path),
            launch=agent.launch.model_copy(update={"registration_revision": 3}),
        )
        == "native-id"
    )
    for changed in (
        agent.launch.model_copy(update={"native_executable": str(tmp_path / "other-native")}),
        agent.launch.model_copy(update={"config_directory": str(tmp_path / "elsewhere")}),
    ):
        with pytest.raises(ApplicationError, match="configuration changed"):
            store.session("harbor", "codex", str(tmp_path), launch=changed)


def test_coordinator_config_path_change_requires_fresh_recovery_and_preserves_chat(
    tmp_path, monkeypatch
):
    service, conversation = setup(tmp_path, monkeypatch)
    store = service.coordinator.store
    config = tmp_path / "new-config"
    config.mkdir()

    async def exercise():
        try:
            first = await settled(
                service, service.coordinator.send("harbor", conversation.id, message())
            )
            assert first.status == "completed" and first.launch.registration_revision == 1
            HarnessSettings(service.workspace).edit("codex", HarnessEdit(expected_revision=1))
            unchanged = await settled(
                service,
                service.coordinator.send("harbor", conversation.id, message("Check continuity")),
            )
            assert unchanged.status == "completed" and unchanged.session == "resumed"
            assert unchanged.launch.registration_revision == 2
            HarnessSettings(service.workspace).edit(
                "codex", HarnessEdit(expected_revision=2, config_directory=str(config))
            )
            failed = await settled(
                service,
                service.coordinator.send(
                    "harbor", conversation.id, message("Continue with the new configuration")
                ),
            )
            assert failed.status == "failed" and failed.session == "unavailable"
            assert not failed.native_started and failed.applied is None
            assert store.page("harbor").session_recovery_turn_id == failed.id
            assert store.session("harbor", "codex", str(tmp_path / "harbor")) == "test-session"
            store.reset_session("harbor", failed.id)
            recovered = await settled(
                service,
                service.coordinator.send(
                    "harbor", conversation.id, message("Continue from the saved chat")
                ),
            )
            assert recovered.status == "completed" and recovered.session == "new"
            assert recovered.launch.registration_revision == 3
            assert recovered.launch.config_directory == str(config)
            assert [turn.id for turn in store.page("harbor").items] == [
                first.id,
                unchanged.id,
                failed.id,
                recovered.id,
            ]
        finally:
            await service.close()

    asyncio.run(exercise())


def test_schema_44_upgrade_preserves_choices_and_recovery_and_is_database_only(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", ())
        execution = fixture(tmp_path)
        workspace = execution.workspace
        AgentSettings(workspace).edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=1, selection=AgentChoice(model="saved-model", effort="high")
            ),
        )
        before = AgentSettings(workspace).get("harbor", "coordinator")
        with workspace.connection(write=True) as db:
            db.execute(
                "INSERT INTO coordinator_sessions VALUES (?,?,?,?)",
                ("harbor", "codex", "old-session", str(tmp_path)),
            )
    # The actual migration cannot inspect host paths, executables or native accounts.
    monkeypatch.setattr(
        harness_host, "resolve", lambda *args: pytest.fail("native detection during migration")
    )
    upgraded = Workspace(workspace.directory)
    assert upgraded.schema_version == migrations.current_version()
    assert AgentSettings(upgraded).get("harbor", "coordinator") == before
    assert len(HarnessSettings(upgraded).all()) == 3
    assert CoordinatorStore(upgraded).session("harbor", "codex", str(tmp_path)) == "old-session"
    backup = storage.backups(upgraded.directory)[0]
    assert backup["schema_version"] == 44
    storage.restore(upgraded.directory, backup["id"])
    assert storage.status(upgraded.directory)["schema_version"] == 44
    assert Workspace(upgraded.directory).schema_version == migrations.current_version()


def test_real_registration_migration_failure_rolls_back_new_table_and_binding_column(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", ())
        workspace = Workspace(tmp_path / "state")
    original = migrations.MIGRATIONS[0]

    def fail(db):
        original.apply(db)
        raise RuntimeError("after real registration DDL")

    monkeypatch.setattr(migrations, "MIGRATIONS", (replace(original, apply=fail),))
    with pytest.raises(ApplicationError, match="rolled back"):
        Workspace(workspace.directory)
    with storage.connect(workspace.database) as db:
        assert storage.version(db) == 44
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='harness_settings'").fetchone()
            is None
        )
        assert [row[1] for row in db.execute("PRAGMA table_info(coordinator_sessions)")] == [
            "project_id",
            "harness",
            "session_id",
            "cwd",
        ]
    monkeypatch.setattr(migrations, "MIGRATIONS", (original,))
    assert Workspace(workspace.directory).schema_version == 45


def test_catalog_migration_failure_preserves_registration_and_recovers(tmp_path, monkeypatch):
    registration_migration, catalog_migration = migrations.MIGRATIONS[:2]
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", (registration_migration,))
        workspace = Workspace(tmp_path / "state")
        saved = HarnessSettings(workspace).edit(
            "claude-code", HarnessEdit(expected_revision=1, executable="/host/claude")
        )

    def fail(db):
        catalog_migration.apply(db)
        raise RuntimeError("after catalog DDL")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            (registration_migration, replace(catalog_migration, apply=fail)),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(workspace.directory)
    with storage.connect(workspace.database) as db:
        assert storage.version(db) == 45
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='harness_catalogs'").fetchone()
            is None
        )
    upgraded = Workspace(workspace.directory)
    assert HarnessSettings(upgraded).get("claude-code") == saved
    backup = storage.backups(upgraded.directory)[0]
    storage.restore(upgraded.directory, backup["id"])
    assert storage.status(upgraded.directory)["schema_version"] == 45
    assert Workspace(upgraded.directory).schema_version == migrations.current_version()


def test_http_host_settings_are_independent_and_do_not_start_models(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWFIELD_UPDATE_CHECKS", "0")
    monkeypatch.setenv("PATH", "")
    app = create_app(data_dir=tmp_path / "state")

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                statuses = (await client.get("/api/harnesses")).json()
                assert [item["registration"]["harness"] for item in statuses] == [
                    "codex",
                    "claude-code",
                    "pi",
                ]
                saved = await client.put(
                    "/api/harnesses/claude-code",
                    json={"expected_revision": 1, "executable": str(tmp_path / "claude")},
                )
                assert saved.status_code == 200 and saved.json()["revision"] == 2
                assert (
                    await client.put("/api/harnesses/claude-code", json={"expected_revision": 1})
                ).status_code == 409
                assert (
                    await client.put(
                        "/api/harnesses/codex",
                        json={"expected_revision": 1, "account": "not-owned"},
                    )
                ).status_code == 422
                assert (await client.get("/api/harnesses/codex")).json()["registration"][
                    "revision"
                ] == 1
                checked = (await client.post("/api/harnesses/claude-code/check")).json()
                assert (
                    checked["checked"]
                    and checked["authentication"] == "unknown"
                    and checked["model_access"] == "unverified"
                )
                assert not app.state.supervisor.catalogs.jobs and not app.state.supervisor.jobs
                ownership = CatalogOwnership(
                    id="interrupted-exact-id",
                    harness="claude-code",
                    project_id=None,
                    status="uncertain",
                    started_at="2026-10-09T00:00:00Z",
                    launch=harness_host.status(
                        app.state.supervisor.workspace.directory,
                        HarnessSettings(app.state.supervisor.workspace).get("claude-code"),
                        {},
                    ).launch,
                )
                with app.state.supervisor.workspace.connection(write=True) as db:
                    db.execute(
                        "INSERT INTO harness_catalogs VALUES (?,?)",
                        ("claude-code", ownership.model_dump_json()),
                    )
                held = (await client.get("/api/harnesses/claude-code")).json()
                assert held["catalog_ownership"]["id"] == ownership.id
                assert "catalog_uncertain" in held["problems"] and not held["selectable"]
                endpoint = "/api/harnesses/claude-code/catalog/confirm-stopped"
                assert (await client.post(endpoint, json={"id": "stale"})).status_code == 409
                assert (await client.get("/api/harnesses/claude-code")).json()["catalog_ownership"]
                cleared = await client.post(endpoint, json={"id": ownership.id})
                assert cleared.status_code == 200 and cleared.json()["catalog_ownership"] is None
                assert not app.state.supervisor.catalogs.jobs

    asyncio.run(exercise())


def test_offline_status_respects_saved_override_without_initializing_missing_state(
    tmp_path, monkeypatch
):
    missing = tmp_path / "missing"
    assert offline_registration(missing, "codex").revision == 1 and not missing.exists()
    workspace = Workspace(tmp_path / "state")
    executable, log = native(tmp_path, "codex")
    saved = HarnessSettings(workspace).edit(
        "codex", HarnessEdit(expected_revision=1, executable=str(executable))
    )
    monkeypatch.setenv("PATH", "")
    result = CliRunner().invoke(
        cli, ["--data-dir", str(workspace.directory), "harness", "status", "codex", "--json"]
    )
    assert result.exit_code == 0, result.output
    status = json.loads(result.stdout)
    assert status["registration"] == saved.model_dump() and status["launch"][
        "native_executable"
    ] == str(executable)
    assert not log.exists() and storage.backups(workspace.directory) == []


def test_cli_host_configuration_targets_service_and_reset_is_revisioned(monkeypatch):
    calls = []

    def request(self, method, path, body=None):
        calls.append((method, path, body))
        return {"revision": 2}

    monkeypatch.setattr(Client, "request", request)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "harness",
            "configure",
            "claude-code",
            "--revision",
            "1",
            "--config-directory",
            "/host/native-config",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "PUT",
        "harnesses/claude-code",
        {"expected_revision": 1, "executable": None, "config_directory": "/host/native-config"},
    )
    result = runner.invoke(cli, ["harness", "configure", "codex", "--revision", "2", "--json"])
    assert result.exit_code == 0 and calls[-1][2]["config_directory"] is None
    result = runner.invoke(cli, ["harness", "settings", "codex", "--json"])
    assert result.exit_code == 0 and calls[-1][:2] == ("GET", "harnesses/codex")
    result = runner.invoke(
        cli, ["harness", "confirm-stopped", "claude-code", "--discovery", "exact-id", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "POST",
        "harnesses/claude-code/catalog/confirm-stopped",
        {"id": "exact-id"},
    )


def test_running_discovery_is_selectable_but_uncertain_cleanup_is_not(tmp_path, monkeypatch):
    executable, log = native(tmp_path, "codex")
    app = create_app(data_dir=tmp_path / "state")

    async def exercise():
        async with app.router.lifespan_context(app):
            workspace = app.state.workspace
            registration = HarnessSettings(workspace).edit(
                "codex", HarnessEdit(expected_revision=1, executable=str(executable))
            )
            detected = harness_host.status(workspace.directory, registration, {})
            assert detected.selectable
            ownership = CatalogOwnership(
                id="owned-discovery",
                harness="codex",
                project_id=None,
                status="running",
                started_at=now(),
                launch=detected.launch,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                for phase in ("running", "uncertain"):
                    ownership.status = phase
                    with workspace.connection(write=True) as db:
                        db.execute(
                            "INSERT OR REPLACE INTO harness_catalogs VALUES (?,?)",
                            ("codex", ownership.model_dump_json()),
                        )
                    result = (await client.get("/api/harnesses/codex")).json()
                    assert result["catalog_ownership"]["status"] == phase
                    assert result["selectable"] is (phase == "running")
                    assert f"catalog_{phase}" in result["problems"]
            assert not log.exists()  # Availability reads never run the native executable.

    asyncio.run(exercise())
