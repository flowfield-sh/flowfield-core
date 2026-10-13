import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("milestone details retain history and prepared context through edits", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: { name: "Milestone walkthrough" },
    })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  const path = join(state, "milestone-agreements");
  mkdirSync(path, { recursive: true });
  const project = await (
    await request.post(`/api${base}/projects`, {
      data: { path, task_prefix: "AGR" },
    })
  ).json();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/milestones`);
  await page
    .getByRole("button", { name: "New milestone", exact: true })
    .click();
  const editor = page.getByRole("region", {
    name: "New milestone",
    exact: true,
  });
  await editor
    .getByLabel("Title", { exact: true })
    .fill("Installed harness integration");
  await editor
    .getByLabel("Description", { exact: true })
    .fill("Support the complete installed journey.");
  await editor
    .getByLabel("Acceptance criteria", { exact: true })
    .fill("Cold installation works without a checkout.");
  await page.screenshot({
    path: testInfo.outputPath("milestone-editor-desktop-light.png"),
    animations: "disabled",
  });
  await editor
    .getByRole("button", { name: "Create milestone", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  const milestone = (
    await (await request.get(`/api${base}/milestones`)).json()
  )[0];
  const taskResponse = await request.post(`/api/projects/${project.id}/tasks`, {
    data: {
      title: "Check installed discovery",
      body: "Verify installed executable discovery.",
      milestone_id: milestone.id,
      stages: fixtureStages(),
      preparation: { completion: "report" },
    },
  });
  expect(taskResponse.ok(), await taskResponse.text()).toBeTruthy();
  const task = await taskResponse.json();
  expect(task.publication.milestone.revision).toBe(1);
  await page.goto(`${base}/milestones/${milestone.key}`);
  const edit = page.getByRole("region", {
    name: "Edit milestone",
    exact: true,
  });
  await edit
    .getByLabel("Acceptance criteria", { exact: true })
    .fill("Cold installation and offline discovery work.");
  await edit.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.goto(`${base}/milestones/${milestone.key}`);
  await edit
    .locator("summary")
    .filter({ hasText: /^Milestone history$/ })
    .click();
  await edit
    .getByRole("button", { name: "View revision 1", exact: true })
    .click();
  await expect(
    edit.getByText("Cold installation works without a checkout.", {
      exact: true,
    }),
  ).toBeVisible();
  await edit.getByLabel("Title", { exact: true }).fill("Unsaved local title");
  const update = await request.put(`/api${base}/milestones/${milestone.id}`, {
    data: { expected_revision: 2, title: "Changed elsewhere" },
  });
  expect(update.ok(), await update.text()).toBeTruthy();
  await expect(edit.getByText(/A newer revision is available/)).toBeVisible();
  await expect(edit.getByLabel("Title", { exact: true })).toHaveValue(
    "Unsaved local title",
  );
  await edit.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.goto(`/projects/${project.id}/tasks/${task.key}`);
  await expect(
    page.getByText("Milestone details changed", { exact: true }),
  ).toBeVisible();
  await page
    .locator("summary")
    .filter({ hasText: /^Milestone at preparation/ })
    .click();
  await expect(
    page.getByText("Cold installation works without a checkout.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/The milestone has changed to revision 3/),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-desktop-light.png"),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await page
    .locator("summary")
    .filter({ hasText: /^Milestone at preparation/ })
    .click();
  await page
    .getByText("Cold installation works without a checkout.", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-narrow-dark.png"),
    animations: "disabled",
  });
  await page.goto(`${base}/milestones/${milestone.key}`);
  await edit
    .getByLabel("Acceptance criteria", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("milestone-editor-narrow-dark.png"),
    animations: "disabled",
  });
  await page.goto(`${base}/inbox`);
  await expect(
    page.getByText("Milestone details changed", { exact: true }),
  ).toBeVisible();
});
