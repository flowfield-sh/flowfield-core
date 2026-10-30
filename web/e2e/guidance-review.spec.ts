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
from flowfield.application import Workspace, TaskCreate
from flowfield.workspaces import Workspaces, WorkspaceCreate
from flowfield.documents import Documents, DocumentEdit
from flowfield.guidance_reconciliation import prepare
from flowfield.task_reviews import TaskReviews
from flowfield.execution import Execution
store = Workspace(Path(sys.argv[1]))
space = Workspaces(store).create(WorkspaceCreate(name="Harbor", **{
    role: {"model": "scripted-fixture"} for role in ("coordinator", "worker", "reviewer")
}))
task = store.create_task(space.id, TaskCreate(title="Review the pilot labels", body="Identify unclear labels and propose concise alternatives.", status="up_next", completion="report"))
store.create_task(space.id, TaskCreate(title="Plan the next accessibility review across navigation, labels and keyboard controls", body="Identify follow-up work.", completion="report"))
blocked = store.create_task(space.id, TaskCreate(title="Check the preview setup", body="Verify the preview.", status="up_next", completion="report"))
Execution(store).task_problem(space.id, blocked.id, "The preview setup command failed. Fix its configuration before continuing.")
Documents(store).edit(space.id, "guidance", DocumentEdit(expected_revision=1, body="Use plain language and accessible labels."))
prepare(TaskReviews(store), space.id, task.id)
print(json.dumps({"workspace": space.id, "task": task.key}))`,
        state,
      ],
      { encoding: "utf8" },
    ),
  );
  await page.goto(`/workspaces/${value.workspace}/tasks`);
  const cards = page.locator(".task-card");
  const backlog = cards.filter({
    hasText: "Plan the next accessibility review",
  });
  await expect(backlog.locator(".work-state")).toHaveText("Not scheduled");
  await expect(backlog.locator(".work-state")).toHaveAttribute(
    "data-tone",
    "idle",
  );
  await expect(
    cards.filter({ hasText: "Review the pilot labels" }).locator(".work-state"),
  ).toHaveText("Queue paused");
  await expect(
    cards.filter({ hasText: "Check the preview setup" }).locator(".work-state"),
  ).toHaveText("Cannot start");
  for (const card of await cards.all()) {
    await expect(card.locator(".work-state")).toHaveCount(1);
    await expect(card.locator("p")).toHaveCount(0);
    await expect(card.getByRole("link")).toHaveCount(1);
  }
  await dialogViews(page, info, "minimal-board");
  await cards.getByRole("link", { name: /Check the preview setup/ }).click();
  await expect(
    page.getByText(
      "The preview setup command failed. Fix its configuration before continuing.",
      { exact: true },
    ),
  ).toBeVisible();
  const notices = page.getByLabel("Task notices", { exact: true });
  await expect(
    notices.getByRole("heading", { name: "Work cannot start", exact: true }),
  ).toBeVisible();
  await expect(
    notices.getByRole("link", { name: "Workspace settings" }),
  ).toHaveCount(0);
  expect(
    await notices.evaluate((element) =>
      Boolean(
        element.compareDocumentPosition(
          document.querySelector(".task-definition")!,
        ) & Node.DOCUMENT_POSITION_FOLLOWING,
      ),
    ),
  ).toBe(true);
  await dialogViews(page, info, "scheduling-blocker");
  await page.goto(`/workspaces/${value.workspace}/tasks/${value.task}`);
  const guidance = page.getByRole("heading", {
    name: "Guidance review",
    exact: true,
  });
  await expect(guidance).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Queue paused", exact: true }).first(),
  ).toBeVisible();
  await dialogViews(page, info, "guidance-queued");
  await page.getByRole("button", { name: "Stop review", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Retry review", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Review stopped; retained evidence/)).toHaveCount(
    0,
  );
  await dialogViews(page, info, "guidance-stopped");
  await page.getByRole("button", { name: "Retry review", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop review", exact: true }),
  ).toBeVisible();
});
