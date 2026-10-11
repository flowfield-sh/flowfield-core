import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("cross-project prerequisites link to their owner and refresh dependent views", async ({
  page,
  request,
}) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: { name: "Shared dependencies" },
    })
  ).json();
  for (const [id, prefix] of [
    ["dependency-core", "DPC"],
    ["dependency-site", "DPS"],
  ]) {
    const path = join(state, id);
    mkdirSync(path, { recursive: true });
    expect(
      (
        await request.post(`/api/workspaces/${workspace.id}/projects`, {
          data: { id, path, task_prefix: prefix },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(`/api/projects/${id}/tasks`, {
          data: {
            id: "same",
            title: `${id} outcome`,
            body: "Agreed report",
            stages: fixtureStages(),
            dependencies: prefix === "DPS" ? ["DPC-1"] : [],
          },
        })
      ).ok(),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/projects/dependency-site/tasks/DPS-1");
  const task = page.getByRole("region", { name: "Task details", exact: true });
  const prerequisite = task
    .getByRole("link", { name: "DPC-1", exact: true })
    .first();
  await expect(prerequisite).toHaveAttribute(
    "href",
    "/projects/dependency-core/tasks/DPC-1",
  );
  await prerequisite.click();
  await expect(page).toHaveURL(/\/projects\/dependency-core\/tasks\/DPC-1$/);
  await expect(
    page.getByRole("heading", {
      name: "DPC-1 · dependency-core outcome",
      exact: true,
    }),
  ).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/projects\/dependency-site\/tasks\/DPS-1$/);
  await expect(task.getByText("Blocked by", { exact: false })).toBeVisible();
  const refreshed = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().includes("/projects/dependency-site/view/tasks/") &&
      response.ok(),
  );
  expect(
    (
      await request.post("/api/projects/dependency-core/tasks/same/progress", {
        data: { expected_revision: 1, status: "done", completion: "report" },
      })
    ).ok(),
  ).toBeTruthy();
  await refreshed;
  await expect(task.getByText("Blocked by", { exact: false })).toHaveCount(0);
});
