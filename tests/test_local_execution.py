"""Local execution outcomes, using real Git/processes and no model or credentials."""

import asyncio
import json
import os
import signal
import sys

import pytest
from test_native_adapters import start

from flowfield.adapters.git_integration import candidate
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.json_rpc import JsonRpc
from flowfield.adapters.local_execution import LocalHost
from flowfield.adapters.local_process import LocalProcess
from flowfield.errors import ApplicationError


def repository(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init")
    (repo / "code.txt").write_text("baseline\n")
    (repo / ".gitignore").write_text("node_modules/\n.venv/\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    return repo, baseline(repo)


def test_host_tools_and_settings_are_available_without_registration(tmp_path):
    repo, base = repository(tmp_path)
    tools = tmp_path / "installed-tools"
    tools.mkdir()
    tool = tools / "already-installed"
    tool.write_text('#!/bin/sh\nprintf "%s" "$PROJECT_SETTING"\n')
    tool.chmod(0o700)
    variables = {
        "PATH": str(tools),
        "HOME": str(tmp_path / "home"),
        "PROJECT_SETTING": "available",
        "TEST_SECRET": "do-not-log",
        "GIT_DIR": str(repo / ".git"),
        "GIT_INDEX_FILE": str(repo / ".git/index"),
        "GIT_OBJECT_DIRECTORY": str(repo / ".git/objects"),
        "GIT_NAMESPACE": "another-attempt",
    }
    host = LocalHost(variables)
    variables["PATH"] = "/changed-after-construction"
    attempt = host.prepare(tmp_path / "state", repo, "one", base)
    env = attempt.launch_environment()
    assert env["PATH"] == str(tools) and env["HOME"].endswith("/home")
    assert "GIT_DIR" not in env and "GIT_INDEX_FILE" not in env
    assert "GIT_OBJECT_DIRECTORY" not in env and "GIT_NAMESPACE" not in env
    assert not (attempt.runtime / "python").exists()
    assert not (attempt.runtime / "bin").exists()
    assert "do-not-log" not in repr(attempt)
    env["PROJECT_SETTING"] = "changed"
    assert attempt.launch_environment()["PROJECT_SETTING"] == "available"

    async def exercise():
        owned = await LocalProcess.start(
            ["already-installed"],
            cwd=attempt.workspace.checkout,
            env=attempt.launch_environment(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(owned.process.communicate(), 3)
            assert stdout == b"available" and stderr == b""
        finally:
            assert await owned.close(timeout=0.3)

    asyncio.run(exercise())
    with pytest.raises(FileExistsError):
        host.prepare(tmp_path / "state", repo, "one", base)
    assert baseline(repo) == base


@pytest.mark.parametrize("identity", ["../escape", "a/b", "", ".", "..", "a\\b"])
def test_invalid_run_identity_cannot_allocate_elsewhere(tmp_path, identity):
    with pytest.raises(ValueError, match="identity"):
        LocalHost({}).prepare(tmp_path, tmp_path, identity, "unused")
    assert list(tmp_path.iterdir()) == []


# Each process independently binds an OS-assigned port and uses its own SQLite file.
# This models cooperating project setup, not automatic isolation of arbitrary apps.
LOCAL_WORK = """
import json, os, socket, sqlite3, sys
from pathlib import Path
root = Path(os.environ['FLOWFIELD_RUNTIME_DIR'])
identity = os.environ['FLOWFIELD_RUN_ID']
Path('code.txt').write_text(identity + '\\n')
Path('node_modules').mkdir()
Path('node_modules/dependency').write_text(identity)
Path(os.environ['TMPDIR'], 'same-name').write_text(identity)
db = sqlite3.connect(root / 'test.db')
db.execute('create table result(value text)')
db.execute('insert into result values (?)', (identity,))
db.commit()
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(json.dumps({'port': sock.getsockname()[1], 'value': identity}), flush=True)
for line in sys.stdin:
    print(db.execute('select value from result').fetchone()[0], flush=True)
"""


def test_parallel_workspaces_runtime_stop_and_conflicting_results(tmp_path):
    repo, base = repository(tmp_path)
    host = LocalHost({"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")})
    first, second = [host.prepare(tmp_path / "state", repo, name, base) for name in ("a", "b")]

    async def exercise():
        owners = []
        try:
            for attempt in (first, second):
                owners.append(
                    await LocalProcess.start(
                        [sys.executable, "-c", LOCAL_WORK],
                        cwd=attempt.workspace.checkout,
                        env=attempt.launch_environment(),
                    )
                )
            lines = await asyncio.wait_for(
                asyncio.gather(*(owner.process.stdout.readline() for owner in owners)), 5
            )
            reports = [json.loads(line) for line in lines]
            assert reports[0]["port"] != reports[1]["port"]
            assert [report["value"] for report in reports] == ["a", "b"]
            assert await owners[0].close(timeout=0.5)
            assert owners[1].process.returncode is None
            owners[1].process.stdin.write(b"still running?\n")
            await owners[1].process.stdin.drain()
            assert await asyncio.wait_for(owners[1].process.stdout.readline(), 2) == b"b\n"
        finally:
            for owner in owners:
                assert await owner.close(timeout=0.5)

    asyncio.run(exercise())
    for attempt in (first, second):
        assert (attempt.runtime / "tmp/same-name").read_text() == attempt.run_id
        assert (
            attempt.workspace.checkout / "node_modules/dependency"
        ).read_text() == attempt.run_id
    result_a, _ = first.workspace.snapshot(base)
    result_b, _ = second.workspace.snapshot(base)
    assert git(repo, "show", f"{result_a}:code.txt") == b"a\n"
    assert git(repo, "show", f"{result_b}:code.txt") == b"b\n"
    with pytest.raises(ApplicationError, match="combine"):
        candidate(repo, result_a, result_b, "combined")
    assert baseline(repo) == base and (repo / "code.txt").read_text() == "baseline\n"
    assert not (repo / "node_modules").exists()


def test_native_sessions_use_prepared_environments_and_stop_independently(tmp_path):
    repo, base = repository(tmp_path)
    host = LocalHost({})

    async def exercise():
        clients, prompts = [], []
        try:
            for identity in ("one", "two"):
                attempt = host.prepare(tmp_path / "state", repo, identity, base)
                started = asyncio.Event()
                events = []
                client = await start(attempt.workspace.checkout, events)
                client.on_activity = lambda value, started=started: (
                    started.set() if "started" in value.text else None
                )
                clients.append(client)
                prompts.append(asyncio.create_task(client.prompt('{"mode":"wait"}', None)))
                await asyncio.wait_for(started.wait(), 3)
            assert await clients[0].stop()
            assert (await prompts[0])["status"] == "stopped"
            assert not clients[1].stopping and not prompts[1].done()
        finally:
            for client in clients:
                await client.close()
            await asyncio.gather(*prompts, return_exceptions=True)

    asyncio.run(exercise())


def test_cancelled_launch_still_stops_its_process(tmp_path, monkeypatch):
    async def exercise():
        spawned, release = asyncio.Event(), asyncio.Event()
        real_spawn = asyncio.create_subprocess_exec
        processes = []

        async def delayed_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            processes.append(process)
            spawned.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
        task = asyncio.create_task(LocalProcess.start(["/bin/sleep", "60"], cwd=tmp_path, env={}))
        await asyncio.wait_for(spawned.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(exercise())


def test_native_cancelled_launch_retains_unconfirmed_process_owner(tmp_path, monkeypatch):
    async def exercise():
        spawned, release = asyncio.Event(), asyncio.Event()
        real_spawn, killpg = asyncio.create_subprocess_exec, os.killpg
        processes = []

        async def delayed_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            processes.append(process)
            spawned.set()
            await release.wait()
            return process

        def deny(group, signum):
            if processes and group == processes[0].pid:
                raise PermissionError("Cannot confirm cleanup")
            killpg(group, signum)

        async def reject(method, params):
            return {}

        client = JsonRpc(lambda *_: None, reject)
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
            patch.setattr(os, "killpg", deny)
            task = asyncio.create_task(client.start(["/bin/sleep", "60"], cwd=tmp_path, env={}))
            try:
                await asyncio.wait_for(spawned.wait(), 3)
                task.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert client.owner.process is processes[0]
                assert not await client.close()
            finally:
                release.set()
                if processes:
                    killpg(processes[0].pid, signal.SIGKILL)
                    await processes[0].wait()

    asyncio.run(exercise())


def test_stop_caller_cancellation_keeps_cleanup_and_does_not_repeat_signals(tmp_path):
    async def exercise():
        owned = await LocalProcess.start(
            [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(15, signal.SIG_IGN); "
                "print('ready',flush=True); time.sleep(60)",
            ],
            cwd=tmp_path,
            env={},
        )
        try:
            await asyncio.wait_for(owned.process.stdout.readline(), 3)
            stop = asyncio.create_task(owned.close(timeout=0.1))
            await asyncio.sleep(0)
            stop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stop
            assert await owned.close(timeout=0.1)
            assert owned.process.returncode == -signal.SIGKILL
            assert await owned.close(timeout=0.1)
        finally:
            await owned.close(timeout=0.1)

    asyncio.run(exercise())


def test_group_stop_covers_an_ordinary_child(tmp_path):
    async def exercise():
        owned = await LocalProcess.start(
            [
                sys.executable,
                "-c",
                "import signal,subprocess; "
                "p=subprocess.Popen(['/bin/sleep','60']); "
                "signal.signal(signal.SIGTERM, lambda *_: p.wait()); "
                "print(p.pid,flush=True); p.wait()",
            ],
            cwd=tmp_path,
            env={},
        )
        try:
            child = int(await asyncio.wait_for(owned.process.stdout.readline(), 3))
            assert await owned.close(timeout=0.5)
            with pytest.raises(ProcessLookupError):
                os.kill(child, 0)
        finally:
            await owned.close(timeout=0.5)

    asyncio.run(exercise())


def test_unconfirmed_group_stop_is_not_reported_as_success(tmp_path, monkeypatch):
    async def exercise():
        owned = await LocalProcess.start(["/bin/sleep", "60"], cwd=tmp_path, env={})
        killpg = os.killpg

        def deny(group, signum):
            if group == owned.process.pid:
                raise PermissionError("No longer able to control this group")
            killpg(group, signum)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(os, "killpg", deny)
                assert not await owned.close(timeout=0.1)
        finally:
            killpg(owned.process.pid, signal.SIGKILL)
            await owned.process.wait()

    asyncio.run(exercise())


def test_group_receipt_does_not_claim_detached_process_containment(tmp_path):
    async def exercise():
        # This deliberate escape is test-owned and always explicitly cleaned up.
        owned = await LocalProcess.start(
            [
                sys.executable,
                "-c",
                "import subprocess; p=subprocess.Popen(['/bin/sleep','60'], "
                "start_new_session=True, stdin=subprocess.DEVNULL, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                "print(p.pid,flush=True); p.wait()",
            ],
            cwd=tmp_path,
            env={},
        )
        child = int(await asyncio.wait_for(owned.process.stdout.readline(), 3))
        try:
            assert await owned.close(timeout=0.5)
            os.kill(child, 0)  # Different session; a group exit is not a sandbox guarantee.
        finally:
            os.kill(child, signal.SIGKILL)
            await owned.close(timeout=0.5)

    asyncio.run(exercise())
