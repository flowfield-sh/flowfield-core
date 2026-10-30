import { projectWorkspace, fixtureProgress } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

test("cross-project prerequisites link to their owner and refresh dependent views", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Shared dependencies",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
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
          data: { id, path },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(
          `/api/workspaces/${await projectWorkspace(page.request, id)}/tasks`,
          {
            data: {
              id,
              project_id: id,
              title: `${id} outcome`,
              body: "Agreed report",
              completion: "report",
              dependencies: prefix === "DPS" ? ["T-1"] : [],
            },
          },
        )
      ).ok(),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "dependency-site")}/tasks/T-2`,
  );
  const task = page.getByRole("region", { name: "Task details", exact: true });
  const prerequisite = task
    .getByRole("link", { name: "T-1", exact: true })
    .first();
  await expect(prerequisite).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(page.request, "dependency-core")}/tasks/T-1`,
  );
  await prerequisite.click();
  await expect(page).toHaveURL(/\/tasks\/T-1$/);
  await expect(
    page.getByRole("heading", {
      name: /dependency-core outcome$/,
    }),
  ).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/tasks\/T-2$/);
  await expect(task.getByLabel("Related work")).toContainText(
    "Prerequisites: T-1",
  );
  await page.screenshot({
    path: testInfo.outputPath("dependency-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(task.getByLabel("Related work")).toContainText(
    "Prerequisites: T-1",
  );
  await page.screenshot({ path: testInfo.outputPath("dependency-mobile.png") });
  const refreshed = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().includes(`/view/tasks/`) &&
      response.ok(),
  );
  const completed = await fixtureProgress(request, {
    project_id: "dependency-core",
    task_id: "dependency-core",
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  // The model-free fixture writes outside the running service. Notify through a
  // real coordinator edit so this assertion observes its normal live refresh.
  const edited = await request.put(
    `/api/workspaces/${workspace.id}/tasks/${completed.id}`,
    {
      data: {
        expected_revision: completed.revision,
        title: "Delivered API outcome",
      },
    },
  );
  expect(edited.ok(), await edited.text()).toBeTruthy();
  await refreshed;
  await expect(
    task.getByRole("heading", { name: "Waiting on", exact: true }),
  ).toHaveCount(0);
});
