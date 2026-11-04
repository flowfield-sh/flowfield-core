"""Resolve a submitted recipe from the Worker's frozen assignment, never later defaults."""

import json
import sqlite3

from flowfield.errors import ApplicationError
from flowfield.execution_models import Run
from flowfield.runtime_models import ResultRecipe


def capture(db: sqlite3.Connection, run: Run) -> ResultRecipe | None:
    if not run.project_id or run.completion != "code":
        return None
    row = db.execute("SELECT assignment FROM runs WHERE id=?", (run.id,)).fetchone()
    sections = json.loads(row[0]) if row else {}
    defaults = json.loads(sections.get("preview", "{}"))
    recipe = {
        "setup_commands": run.setup_commands,
        "setup_timeout_seconds": run.setup_timeout_seconds,
        **defaults,
    }
    if run.result and run.result.recipe is not None:
        supplied = run.result.recipe.model_dump(exclude_unset=True)
        if supplied.get("run_command"):
            recipe["no_preview_reason"] = ""
        elif supplied.get("no_preview_reason"):
            recipe.update(run_command="", ready_command="", url="", outputs=[])
        recipe.update(supplied)
    resolved = ResultRecipe.model_validate(recipe)
    if (defaults.get("run_command") or defaults.get("no_preview_reason")) and not (
        resolved.run_command or resolved.no_preview_reason
    ):
        raise ApplicationError(
            "result_recipe_required",
            "Provide a result run command or explain why no runnable result applies.",
        )
    return resolved
