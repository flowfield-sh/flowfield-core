import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("milestone agreements retain sources and prepared work through edits", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: { name: "Milestone agreements" },
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
  const document = await (
    await request.post(`/api${base}/documents`, {
      data: {
        title: "Installation contract",
        body: "Install without a checkout.",
      },
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
  await editor
    .getByLabel("Source URL", { exact: true })
    .fill("https://example.test/issues/20");
  await editor
    .locator("summary")
    .filter({ hasText: /^Milestone sources/ })
    .click();
  await editor
    .getByRole("checkbox", { name: "Installation contract", exact: true })
    .check();
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
  expect(milestone.documents[0].revision).toBe(1);
  expect(milestone.documents[0].id).toBe(document.id);
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
    .filter({ hasText: /^Agreement history$/ })
    .click();
  await edit
    .getByRole("button", { name: "View revision 1", exact: true })
    .click();
  await expect(
    edit.getByText("Cold installation works without a checkout.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    edit.getByRole("link", {
      name: "Installation contract · revision 1",
      exact: true,
    }),
  ).not.toBeVisible();
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
    page.getByText("Newer source revision available", { exact: true }),
  ).toBeVisible();
  await page
    .locator("summary")
    .filter({ hasText: /^Milestone agreement/ })
    .click();
  await expect(
    page.getByText("Cold installation works without a checkout.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText(/Revision 3 is available/)).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-desktop-light.png"),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await page
    .locator("summary")
    .filter({ hasText: /^Milestone agreement/ })
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
  await request.put(`/api${base}/documents/${document.id}`, {
    data: { expected_revision: 1, body: "Also check offline discovery." },
  });
  await page.reload();
  await edit
    .locator("summary")
    .filter({ hasText: /^Milestone sources/ })
    .click();
  await edit
    .getByRole("button", {
      name: "Use revision 2 of Installation contract",
      exact: true,
    })
    .click();
  await edit.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(edit).not.toBeVisible();
  const latest = await (
    await request.get(`/api${base}/milestones/${milestone.id}`)
  ).json();
  expect(latest.documents[0].revision).toBe(2);
  const stillPrepared = await (
    await request.get(`/api/projects/${project.id}/tasks/${task.key}`)
  ).json();
  expect(
    stillPrepared.publication.documents.find(
      (item: { id: string }) => item.id === document.id,
    ).revision,
  ).toBe(1);
  await page.goto(`${base}/inbox`);
  await expect(
    page.getByText("Newer source revision available", { exact: true }),
  ).toBeVisible();
});
