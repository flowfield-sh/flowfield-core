"""Upgrade populated project state and durable files without changing their identity."""

import pytest
from execution_fixtures import BASE, fixture
from storage_fixtures import logical_data

from flowfield import migrations
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerResult
from flowfield.migrations import Migration
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.work_scopes import workspace_scope


def test_scope_upgrade_preserves_files_and_rolls_back_whole_graph(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 80)
        )
        execution = fixture(tmp_path)
        run = execution.claim("harbor", BASE, {})
        runtime = tmp_path / "runtime"
        (runtime / "output").mkdir(parents=True)
        (runtime / "output" / "report.txt").write_text("Retain these bytes")
        execution.save_local(run.id, {"runtime": str(runtime)})
        execution.started("harbor", run.id)
        report = WorkerResult(
            summary="Report", checks="Read the file", files=[OutputSelection(path="report.txt")]
        )
        execution.report_result("harbor", run.id, report)
        execution.finish("harbor", run.id, "in_review", result=report, commit=BASE)
    directory = execution.workspace.directory
    before = logical_data(directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 80)

    def fail(db):
        migration.apply(db)
        raise ValueError("scope migration interruption")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(80, fail) if m.version == 80 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(directory)
    assert logical_data(directory) == before
    store = Workspace(directory)
    files = Outputs(store).page("harbor").items
    assert len(files) == 1 and Outputs(store).content(files[0]).read_text() == "Retain these bytes"
    assert files[0].scope_id == "harbor" and files[0].result_id
    assert store.work_scope(workspace_scope("harbor")).project_id is None
    assert store.work_scope("harbor").project_id == "harbor"
    with store.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "work_scopes" in {row[2] for row in db.execute("PRAGMA foreign_key_list(tasks)")}
        assert len(db.execute("SELECT * FROM runs").fetchall()) == 1
