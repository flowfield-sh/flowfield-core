import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

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
