import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("coordinator labels update read-only task headers and preserve deletion history", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", { data: { name: "Label journey" } })
  ).json();
  const path = join(state, "labels-core");
  mkdirSync(path, { recursive: true });
  const project = await (
    await request.post(`/api/workspaces/${workspace.id}/projects`, {
      data: { path, task_prefix: "LBL" },
    })
  ).json();
  const labelPath = `/api/workspaces/${workspace.id}/labels`;
  const custom = await (
    await request.post(labelPath, {
      data: {
        name: "Release candidate",
        color: "green",
        guidance: "Work needed for the next release.",
      },
    })
  ).json();
  const task = await (
    await request.post(`/api/projects/${project.id}/tasks`, {
      data: {
        title: "Prepare the release notes",
        labels: ["bug", custom.id],
        stages: fixtureStages(),
      },
    })
  ).json();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/projects/${project.id}/tasks/${task.key}`);
  const labels = page.locator(".task-header-badges");
  await expect(labels).toContainText("Release candidate");
  await expect(
    page.getByRole("button", { name: /Edit labels|Choose labels|Save labels/ }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("labels-desktop-light.png"),
  });
  expect(
    (
      await request.put(`${labelPath}/${custom.id}`, {
        data: { expected_revision: 1, name: "Release work" },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(labels).toContainText("Release work");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(labels).toContainText("Release work");
  await page.screenshot({
    path: testInfo.outputPath("labels-narrow-dark.png"),
  });
  expect(
    (
      await request.delete(`${labelPath}/${custom.id}`, {
        data: { expected_revision: 2 },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(labels).not.toContainText("Release work");
  const history = await (
    await request.get(`/api/projects/${project.id}/tasks/${task.id}`)
  ).json();
  expect(history.revisions[0].labels).toContain(custom.id);
  await page.goto(`/workspaces/${workspace.id}/edit`);
  await expect(
    page.getByRole("tab", { name: "Labels", exact: true }),
  ).toHaveCount(0);
  await page.goto(`/projects/${project.id}/tasks/new`);
  await expect(
    page.getByRole("heading", { name: "Task not found", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Create task", exact: true }),
  ).toHaveCount(0);
});
