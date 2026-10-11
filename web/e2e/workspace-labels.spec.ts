import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("optional shared labels keep their names and deletion history", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", { data: { name: "Label journey" } })
  ).json();
  const path = join(state, "labels-core");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await request.post(`/api/workspaces/${workspace.id}/projects`, {
        data: { path, task_prefix: "LBL" },
      })
    ).ok(),
  ).toBeTruthy();
  const custom = await (
    await request.post(`/api/workspaces/${workspace.id}/labels`, {
      data: {
        name: "Release candidate",
        color: "green",
        guidance: "Work needed for the next release.",
      },
    })
  ).json();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/projects/labels-core");
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page
    .getByLabel("Title", { exact: true })
    .fill("Prepare the release notes");
  await page.getByRole("button", { name: "Choose labels" }).click();
  await page.getByRole("checkbox", { name: "Bug", exact: true }).check();
  await page
    .getByRole("checkbox", { name: "Release candidate", exact: true })
    .check();
  await expect(
    page.getByText("Work needed for the next release.", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("labels-desktop-light.png"),
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toContainText("Release candidate");
  const tasks = await (
    await request.get("/api/projects/labels-core/tasks")
  ).json();
  expect(tasks[0].labels.sort()).toEqual(["bug", custom.id].sort());
  await request.delete(`/api/workspaces/${workspace.id}/labels/${custom.id}`, {
    data: { expected_revision: 1 },
  });
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).not.toContainText("Release candidate");
  const history = await (
    await request.get(`/api/projects/labels-core/tasks/${tasks[0].id}`)
  ).json();
  expect(history.revisions[0].labels).toContain(custom.id);
  await page.goto("/projects/labels-core/tasks/new");
  await page.getByRole("button", { name: "Choose labels" }).click();
  await expect(
    page.getByRole("checkbox", { name: "Release candidate", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await page.getByRole("button", { name: "Choose labels" }).click();
  await expect(
    page.getByRole("checkbox", { name: "Bug", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("labels-narrow-dark.png"),
  });
  await page.keyboard.press("Escape");
  await page.getByLabel("Title", { exact: true }).fill("Unclassified task");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "LBL-2 · Unclassified task",
      exact: true,
    }),
  ).toBeVisible();
  const current = await (
    await request.get("/api/projects/labels-core/tasks")
  ).json();
  expect(
    current.find(
      (item: { title: string }) => item.title === "Unclassified task",
    ).labels,
  ).toEqual([]);
});

test("workspace label management and existing task edits preserve concurrent changes", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", { data: { name: "Label settings" } })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  const path = join(state, "label-settings");
  mkdirSync(path, { recursive: true });
  await request.post(`/api${base}/projects`, {
    data: { id: "label-settings", path, task_prefix: "LST" },
  });
  const createdTask = await request.post("/api/projects/label-settings/tasks", {
    data: {
      title: "Classify existing work",
      body: "Describe the release scope.",
      stages: fixtureStages(),
    },
  });
  expect(createdTask.ok(), await createdTask.text()).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/edit/labels`);
  await page.getByRole("button", { name: "New label", exact: true }).click();
  await page.getByLabel("Label name", { exact: true }).fill("Release");
  await page.getByLabel("Color", { exact: true }).selectOption("green");
  await page
    .getByLabel("Guidance", { exact: true })
    .fill("Required for the next release.");
  await page.getByRole("button", { name: "Create label", exact: true }).click();
  await page
    .getByRole("button", { name: "Edit Release label", exact: true })
    .click();
  await page
    .getByLabel("Guidance", { exact: true })
    .fill("My unsaved guidance.");
  const catalog = await (await request.get(`/api${base}/labels`)).json();
  const release = catalog.find(
    (item: { name: string }) => item.name === "Release",
  );
  await request.put(`/api${base}/labels/${release.id}`, {
    data: {
      expected_revision: release.revision,
      name: "Release work",
      guidance: "Updated elsewhere.",
    },
  });
  await expect(page.getByText(/A newer revision is available/)).toBeVisible();
  await expect(page.getByLabel("Guidance", { exact: true })).toHaveValue(
    "My unsaved guidance.",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Load latest", exact: true }).click();
  await expect(page.getByLabel("Label name", { exact: true })).toHaveValue(
    "Release work",
  );
  await expect(page.getByLabel("Guidance", { exact: true })).toHaveValue(
    "Updated elsewhere.",
  );
  await page.getByLabel("Color", { exact: true }).selectOption("violet");
  await page.getByRole("button", { name: "Save label", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Edit Release work label", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("label-settings-desktop-light.png"),
  });
  await page.keyboard.press("Escape");
  await page
    .getByRole("link", { name: "LST-1 Classify existing work", exact: true })
    .click();
  await page
    .locator("summary")
    .filter({ hasText: /^Labels$/ })
    .click();
  await page
    .getByRole("button", { name: "Choose labels", exact: true })
    .click();
  await page
    .getByRole("checkbox", { name: "Release work", exact: true })
    .check();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Save labels", exact: true }).click();
  await expect(page.locator(".entity-labels")).toContainText("Release work");
  const task = await (
    await request.get("/api/projects/label-settings/view/tasks/LST-1")
  ).json();
  expect(task.labels).toEqual([release.id]);
  await page
    .getByRole("button", { name: "Back to board", exact: true })
    .click();
  await page.goto(`${base}/edit/labels`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await page
    .getByRole("button", { name: "Edit Release work label", exact: true })
    .click();
  await expect(page.getByLabel("Color", { exact: true })).toHaveValue("violet");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("label-editor-narrow-dark.png"),
  });
  await page.getByRole("button", { name: "Delete label", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Delete label", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Edit Release work label", exact: true }),
  ).toHaveCount(0);
  const current = await (
    await request.get("/api/projects/label-settings/view/tasks/LST-1")
  ).json();
  expect(current.labels).toEqual([]);
  expect(current.agreement_revision).toBe(task.agreement_revision);
});
