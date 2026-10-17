import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state, connectMcp } from "./support";
import { openResource } from "./shell-navigation";

test("workspace files link exact task and milestone handoffs without a project", async ({
  page,
  request,
}, testInfo) => {
  const value = JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/file_browser_fixture.py"), state],
      { encoding: "utf8" },
    ),
  );
  const base = `/workspaces/${value.workspace}`;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/files`);
  const files = page.getByRole("region", { name: "Files", exact: true });
  await expect(
    files.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await expect(
    files.getByText("checklist-draft.txt", { exact: true }),
  ).toBeVisible();
  await expect(files).toContainText("Blocked");
  expect(await (await request.get(`/api${base}/projects`)).json()).toEqual([]);
  const download = page.waitForEvent("download");
  await files
    .locator(".project-list-row")
    .filter({ hasText: "launch-report.txt" })
    .getByRole("link", { name: "Download", exact: true })
    .click();
  expect((await download).suggestedFilename()).toBe("launch-report.txt");
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    if (theme === "dark")
      await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    else await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
    await page.screenshot({
      path: testInfo.outputPath(`files-${width}-${theme}.png`),
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "light" });
  await files
    .locator(".project-list-row")
    .filter({ hasText: "launch-report.txt" })
    .getByRole("link", { name: /W-1: Prepare launch research/ })
    .click();
  await expect(page).toHaveURL(
    `${base}/tasks/${value.task}/changes/${value.result}`,
  );
  await expect(
    page.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("file-task-completed.png"),
  });
  await page.goto(
    `${base}/tasks/${value.partial_task}/changes/${value.partial_result}`,
  );
  await expect(
    page.getByText("checklist-draft.txt", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continue work", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("file-task-partial.png") });
  await page.goto(`${base}/milestones/${value.milestone}`);
  await expect(
    page.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("file-milestone-ready.png"),
  });
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("file-milestone-review.png"),
  });
  await page
    .getByRole("button", { name: "Accept milestone", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("file-milestone-accepted.png"),
  });
  const call = await connectMcp(request);
  await call("edit_milestone", {
    workspace_id: value.workspace,
    milestone_id: value.milestone,
    changes: {
      expected_revision: 1,
      acceptance_criteria: "Add a second market comparison.",
    },
  });
  await page.reload();
  await expect(
    page.getByText(/This report checked milestone revision/),
  ).toBeVisible();
  await expect(
    page.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("file-milestone-stale.png"),
  });
  await openResource(page, "Files");
  await expect(
    files.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
});
