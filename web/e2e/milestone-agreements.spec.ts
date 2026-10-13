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
    name: "Milestone",
    exact: true,
  });
  await edit
    .getByRole("button", { name: "Edit milestone", exact: true })
    .click();
  await edit
    .getByLabel("Acceptance criteria", { exact: true })
    .fill("Cold installation and offline discovery work.");
  await edit.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.goto(`${base}/milestones/${milestone.key}`);
  await edit.getByRole("link", { name: "Revision 1", exact: true }).click();
  await expect(page).toHaveURL(new RegExp("/revisions/1$"));
  await expect(
    edit.getByText("Cold installation works without a checkout.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(edit.getByRole("textbox")).toHaveCount(0);
  await expect(page.locator(".three-column-board")).not.toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("milestone-history-desktop-light.png"),
  });
  await edit
    .getByRole("link", { name: "Current milestone", exact: true })
    .click();
  await edit
    .getByRole("button", { name: "Edit milestone", exact: true })
    .click();
  await edit.getByLabel("Title", { exact: true }).fill("Unsaved local title");
  await edit.getByRole("link", { name: "Revision 1", exact: true }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(edit.getByLabel("Title", { exact: true })).toHaveValue(
    "Unsaved local title",
  );
  const update = await request.put(`/api${base}/milestones/${milestone.id}`, {
    data: { expected_revision: 2, title: "Changed elsewhere" },
  });
  expect(update.ok(), await update.text()).toBeTruthy();
  await expect(edit.getByText(/A newer revision is available/)).toBeVisible();
  await expect(edit.getByLabel("Title", { exact: true })).toHaveValue(
    "Unsaved local title",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await edit.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.goto(`/projects/${project.id}/tasks/${task.key}`);
  await expect(
    page.getByText("Milestone details changed", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Current task definition", { exact: true }),
  ).toHaveText("DefinitionVerify installed executable discovery.");
  await expect(
    page.getByText("Cold installation works without a checkout.", {
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Prepared with revision 1" }),
  ).toHaveAttribute("href", new RegExp("/revisions/1$"));
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-desktop-light.png"),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-narrow-dark.png"),
    animations: "disabled",
  });
  await page.goto(`${base}/milestones/${milestone.key}`);
  await expect(
    edit.getByText("Cold installation and offline discovery work.", {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("milestone-editor-narrow-dark.png"),
    animations: "disabled",
  });
  await page.goto(`${base}/inbox`);
  await expect(
    page.getByText("Milestone details changed", { exact: true }),
  ).toBeVisible();
});
