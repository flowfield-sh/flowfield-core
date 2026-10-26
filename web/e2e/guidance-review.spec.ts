import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";
import { dialogViews } from "./dialog-evidence";

test("changed guidance has visible queue and stop recovery without starting a worker", async ({
  page,
}, info) => {
  const value = JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        "-c",
        `import json, sys
from pathlib import Path
from flowfield.application import Workspace, TaskCreate, TaskPreparation
from flowfield.workspaces import Workspaces, WorkspaceCreate
from flowfield.documents import Documents, DocumentEdit
from flowfield.guidance_reconciliation import prepare
from flowfield.task_reviews import TaskReviews
store = Workspace(Path(sys.argv[1]))
space = Workspaces(store).create(WorkspaceCreate(name="Harbor", **{
    role: {"model": "scripted-fixture"} for role in ("coordinator", "worker", "reviewer")
}))
task = store.create_task(space.id, TaskCreate(title="Review the pilot labels", body="Identify unclear labels and propose concise alternatives.", status="up_next", preparation=TaskPreparation(completion="report")))
Documents(store).edit(space.id, "guidance", DocumentEdit(expected_revision=1, body="Use plain language and accessible labels."))
prepare(TaskReviews(store), space.id, task.id)
print(json.dumps({"workspace": space.id, "task": task.key}))`,
        state,
      ],
      { encoding: "utf8" },
    ),
  );
  await page.goto(`/workspaces/${value.workspace}/tasks/${value.task}`);
  const guidance = page.getByRole("heading", {
    name: "Guidance review",
    exact: true,
  });
  await expect(guidance).toBeVisible();
  await expect(
    page
      .getByRole("img", { name: "Guidance review paused", exact: true })
      .first(),
  ).toBeVisible();
  await dialogViews(page, info, "guidance-queued");
  await page.getByRole("button", { name: "Stop review", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Retry review", exact: true }),
  ).toBeVisible();
  await dialogViews(page, info, "guidance-stopped");
  await page.getByRole("button", { name: "Retry review", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop review", exact: true }),
  ).toBeVisible();
});
