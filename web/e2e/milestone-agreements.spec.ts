import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("milestone reading preserves prepared revisions and renders acceptance lists", async ({
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
      data: { path, name: "fx demo", task_prefix: "FXD" },
    })
  ).json();
  const criteria =
    "- Cold installation works without a checkout.\n  - Executable discovery works.\n\n1. Configure the harness.\n2. Run the smoke check.\n\n- [ ] Complete the walkthrough.";
  const milestone = await (
    await request.post(`/api${base}/milestones`, {
      data: {
        title: "Installed harness integration",
        body: "Support the complete installed journey.",
        acceptance_criteria: criteria,
      },
    })
  ).json();
  const response = await request.post(`/api/projects/${project.id}/tasks`, {
    data: {
      title: "Verify fx discovery and setup errors",
      body: "Verify installed executable discovery.",
      labels: ["feature"],
      milestone_id: milestone.id,
      stages: fixtureStages(),
      preparation: { completion: "report" },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const task = await response.json();
  expect(task.publication.milestone.revision).toBe(1);
  expect(
    (
      await request.put(`/api${base}/milestones/${milestone.id}`, {
        data: {
          expected_revision: 1,
          acceptance_criteria:
            "- Cold installation and offline discovery work.",
        },
      })
    ).ok(),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/milestones/${milestone.key}/revisions/1`);
  const pane = page.getByRole("region", { name: "Milestone", exact: true });
  await expect(pane.getByRole("textbox")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Edit milestone|New milestone/ }),
  ).toHaveCount(0);
  await expect(
    pane.locator(".markdown ul:not(.contains-task-list)").first(),
  ).toHaveCSS("list-style-type", "disc");
  await expect(pane.locator(".markdown ul ul")).toHaveCSS(
    "list-style-type",
    "circle",
  );
  await expect(pane.locator(".markdown ol")).toHaveCSS(
    "list-style-type",
    "decimal",
  );
  await expect(pane.locator(".markdown .task-list-item")).toHaveCSS(
    "list-style-type",
    "none",
  );
  const title = await page
    .getByRole("heading", { name: /Installed harness integration/ })
    .boundingBox();
  const action = await page
    .getByRole("link", { name: "View tasks", exact: true })
    .boundingBox();
  expect(action!.x).toBeGreaterThan(title!.x + title!.width);
  await page.screenshot({
    path: testInfo.outputPath("milestone-history-desktop-light.png"),
  });
  await page.goto(`/projects/${project.id}/tasks/${task.key}`);
  const taskPane = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    taskPane.getByRole("heading", {
      name: "Verify fx discovery and setup errors",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    taskPane.getByText("Milestone details changed", { exact: true }),
  ).toHaveCount(0);
  await expect(
    taskPane.getByLabel("Current task definition", { exact: true }),
  ).toHaveText("DefinitionVerify installed executable discovery.");
  await expect(taskPane.locator(".task-header-badges")).toContainText(
    "Feature",
  );
  await expect(
    taskPane.getByRole("button", {
      name: /^(Edit|Archive|Restore|Edit labels|Choose for Up next)$/,
    }),
  ).toHaveCount(0);
  const badge = taskPane.getByRole("link", { name: "M-1", exact: true });
  await expect(badge).toHaveAttribute(
    "href",
    `${base}/milestones/M-1/revisions/1`,
  );
  await badge.hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "Prepared with revision 1.",
  );
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-desktop-light.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(taskPane).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("milestone-task-narrow-dark.png"),
  });
  await badge.click();
  await expect(page).toHaveURL(new RegExp("/revisions/1$"));
  await expect(
    pane.getByText("Cold installation works without a checkout."),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("milestone-history-narrow-dark.png"),
  });
  await page.goto(base);
  await expect(
    page.getByText("Milestone details changed", { exact: true }),
  ).toBeVisible();
});
