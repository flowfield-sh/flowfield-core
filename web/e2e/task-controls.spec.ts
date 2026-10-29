import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";
import { dialogViews } from "./dialog-evidence";

function seed(active = false) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        "-c",
        `import json, sys
from pathlib import Path
from flowfield.application import Workspace, TaskCreate, TaskPreparation
from flowfield.workspaces import Workspaces, WorkspaceCreate
from flowfield.labels import Labels, LabelCreate
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit
store = Workspace(Path(sys.argv[1]))
space = Workspaces(store).create(WorkspaceCreate(name="Release planning", **{role: {"model": "scripted-fixture"} for role in ("coordinator", "worker", "reviewer")}))
label = Labels(store).create(space.id, LabelCreate(name="Polish", guidance="Improve clarity and consistency", color="slate"))
task = store.create_task(space.id, TaskCreate(title="Polish the task experience", body="Make task controls clear and consistent.", acceptance_criteria="Every change is visible in the task feed.", status="up_next", preparation=TaskPreparation(completion="report")))
if sys.argv[2] == "active":
    execution = Execution(store)
    settings = execution.settings(space.id)
    execution.queue(space.id, QueueEdit(expected_revision=settings.revision, enabled=True))
    execution.claim(space.id, "", {}, task_id=task.id)
    settings = execution.settings(space.id)
    execution.queue(space.id, QueueEdit(expected_revision=settings.revision, enabled=False))
print(json.dumps({"workspace": space.id, "task": task.key, "id": task.id, "label": label.id}))`,
        state,
        active ? "active" : "idle",
      ],
      { encoding: "utf8" },
    ),
  );
}

test("task editing saves definition fields, retains drafts and recovers stale saves", async ({
  page,
  request,
}, info) => {
  const value = seed();
  const base = `/workspaces/${value.workspace}`;
  const href = `${base}/tasks/${value.task}`;
  await page.goto(href);
  const docs = page.getByRole("link", { name: "Documentation", exact: true });
  await expect(docs).toHaveAttribute("href", "https://docs.flowfield.sh");
  await expect(docs).toHaveAttribute("target", "_blank");
  const favicon = await request.get("/favicon.svg");
  expect(favicon.ok()).toBe(true);
  expect(await favicon.text()).toContain("<svg");
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute(
    "href",
    "/favicon.svg",
  );
  await expect(
    page.getByRole("button", { name: "Stop task", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Edit task", exact: true }).click();
  await expect(page).toHaveURL(`${href}/edit`);
  const editor = page.getByRole("region", { name: "Edit task", exact: true });
  await expect(
    editor.getByLabel("Acceptance criteria", { exact: true }),
  ).toHaveValue("Every change is visible in the task feed.");
  await editor
    .getByLabel("Task title", { exact: true })
    .fill("Polish task controls for release");
  await editor
    .getByLabel("Description", { exact: true })
    .fill("Make edits and stopping easy to find.");
  await editor
    .getByLabel("Acceptance criteria", { exact: true })
    .fill("Save all four supported fields together.");
  await editor.getByRole("checkbox", { name: "Polish", exact: true }).check();
  await expect(editor.getByLabel("Project", { exact: true })).toHaveCount(0);
  await dialogViews(page, info, "task-editor");
  let releaseSave!: () => void;
  const saveGate = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  const savePath = `**/api${base}/view/tasks/${value.id}`;
  await page.route(savePath, async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    await saveGate;
    await route.fulfill({
      status: 503,
      json: {
        error: {
          code: "temporarily_unavailable",
          message: "Saving is temporarily unavailable.",
        },
      },
    });
  });
  await editor.getByRole("button", { name: "Save task", exact: true }).click();
  await expect(editor.getByLabel("Task title", { exact: true })).toBeDisabled();
  await dialogViews(page, info, "task-editor-pending");
  releaseSave();
  await expect(editor.getByLabel("Task title", { exact: true })).toBeEnabled();
  await expect(
    page.getByText("Saving is temporarily unavailable.", { exact: false }),
  ).toBeVisible();
  await expect(editor.getByLabel("Task title", { exact: true })).toHaveValue(
    "Polish task controls for release",
  );
  await dialogViews(page, info, "task-editor-failed");
  await page.unroute(savePath);
  await editor.getByRole("button", { name: "Save task", exact: true }).click();
  await expect(page).toHaveURL(href);
  await expect(page.locator(".task-summary")).toContainText(
    "Polish task controls for release",
  );
  await expect(page.getByLabel("Current task definition")).toContainText(
    "Save all four supported fields together.",
  );
  await expect(page.getByLabel("Task feed")).toContainText(
    "Task definition updated",
  );
  const saved = await (
    await request.get(`/api${base}/tasks/${value.id}`)
  ).json();
  expect(saved.labels).toEqual([value.label]);
  await page.getByRole("button", { name: "Edit task", exact: true }).click();
  await editor
    .getByLabel("Task title", { exact: true })
    .fill("My retained edit");
  const update = await request.put(`/api${base}/tasks/${value.id}`, {
    data: { expected_revision: saved.revision, title: "Coordinator update" },
  });
  expect(update.ok()).toBe(true);
  await expect(editor.getByLabel("Task title", { exact: true })).toHaveValue(
    "My retained edit",
  );
  await editor.getByRole("button", { name: "Save task", exact: true }).click();
  await expect(
    editor.getByRole("button", { name: "Load latest", exact: true }),
  ).toBeVisible();
  await dialogViews(page, info, "task-editor-stale");
  await editor
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  const discard = page.getByRole("alertdialog");
  await discard
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect(editor.getByLabel("Task title", { exact: true })).toHaveValue(
    "Coordinator update",
  );
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page).toHaveURL(href);
  await page.goto(`${href}/edit`);
  await expect(editor.getByLabel("Task title", { exact: true })).toHaveValue(
    "Coordinator update",
  );
});

test("task header stop confirms and returns active work to Backlog", async ({
  page,
  request,
}, info) => {
  const value = seed(true);
  const base = `/workspaces/${value.workspace}`;
  await page.goto(`${base}/tasks/${value.task}`);
  const stop = page.getByRole("button", { name: "Stop task", exact: true });
  await stop.click();
  const dialog = page.getByRole("alertdialog");
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await expect(dialog).toContainText(
    "Recorded work, files and history are retained.",
  );
  await dialogViews(page, info, "task-stop");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(
    (await (await request.get(`/api${base}/tasks/${value.id}`)).json()).status,
  ).toBe("in_progress");
  await page
    .getByRole("button", { name: "Task settings", exact: true })
    .click();
  const review = page.getByRole("region", { name: "Reviewer settings" });
  const autoReview = review.getByLabel("Automatically assess task outcomes");
  await expect(autoReview).toBeChecked();
  await autoReview.uncheck();
  await expect(stop).toBeEnabled();
  await stop.click();
  await dialog.getByRole("button", { name: "Stop task", exact: true }).click();
  await expect(stop).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/tasks/${value.id}`)).json())
          .status,
    )
    .toBe("backlog");
  await expect(autoReview).not.toBeChecked();
  await page.getByRole("button", { name: "View task", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(page.getByLabel("Task feed")).toContainText("Moved to Backlog");
  await dialogViews(page, info, "task-stopped");
});
