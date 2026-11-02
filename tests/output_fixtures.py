"""Running Worker with a disposable output directory for retained-file checks."""

from execution_fixtures import BASE, fixture

from flowfield.workspaces import Workspaces


def prepared(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)
    runtime = tmp_path / "attempt" / "runtime"
    (runtime / "output").mkdir(parents=True)
    execution.save_local(run.id, {"runtime": str(runtime)})
    owner = Workspaces(execution.workspace).for_project("harbor").id
    return execution, run, runtime / "output", owner
