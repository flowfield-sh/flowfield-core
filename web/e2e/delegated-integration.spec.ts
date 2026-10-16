import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import { test, state, fixtureProject, projectWorkspace } from "./support";

test("human grants and revokes named milestone integration with retained settings", async ({
  page,
  request,
}, testInfo) => {
  const path = join(state, "delegated-integration");
  mkdirSync(path, { recursive: true });
  const git = (...args: string[]) => execFileSync("git", args, { cwd: path });
  git("init", "-b", "main");
  writeFileSync(join(path, "README.md"), "Existing project\n");
  git("add", "README.md");
  git(
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@example.test",
    "commit",
    "-m",
    "Baseline",
  );
  expect(
    (
      await fixtureProject(request, { data: { path, task_prefix: "DLI" } })
    ).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, "delegated-integration");
  const configured = await request.put(
    "/api/projects/delegated-integration/integration",
    { data: { expected_revision: 1, target_branch: "main", checks: ["true"] } },
  );
  expect(configured.ok(), await configured.text()).toBe(true);
  const milestone = await (
    await request.post(`/api/workspaces/${workspace}/milestones`, {
      data: {
        title: "Reviewed milestone integration",
        acceptance_criteria: "Both changes work.",
      },
    })
  ).json();
  const base = `/workspaces/${workspace}/milestones/${milestone.id}`;
  const response = await request.put(`/api${base}/delivery`, {
    data: {
      expected_revision: 1,
      project_id: "delegated-integration",
      branch: "flowfield/reviewed-feature",
    },
  });
  expect(response.ok(), await response.text()).toBe(true);
  await page.goto(`/workspaces/${workspace}/milestones/${milestone.key}`);
  await expect(
    page.getByText("Documents captured by tasks", { exact: true }),
  ).toHaveCount(0);
  await page
    .locator(".editor-heading")
    .getByRole("link", { name: "Settings", exact: true })
    .click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(
    page.getByRole("tab", { name: "Integration", exact: true }),
  ).toBeVisible();
  const form = page.getByRole("region", { name: "Task integration settings" });
  const checkbox = form.getByRole("checkbox");
  await expect(checkbox).not.toBeChecked();
  await expect(form).toContainText("flowfield/reviewed-feature");
  await checkbox.check();
  await page.getByRole("tab", { name: "Reviewer", exact: true }).click();
  await expect(
    page.getByRole("tabpanel", { name: "Reviewer", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Policy revision|Verification plan/)).toHaveCount(
    0,
  );
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(checkbox).toBeChecked();
  await expect(
    page
      .getByRole("tablist", { name: "Open resources" })
      .getByRole("tab", { selected: true }),
  ).toContainText("Settings");
  await page
    .getByRole("navigation", { name: "Work", exact: true })
    .getByRole("link", {
      name: `${milestone.key} ${milestone.title}`,
      exact: true,
    })
    .click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(checkbox).toBeChecked();
  await form.getByRole("button", { name: "Save integration policy" }).click();
  await expect(
    form.getByRole("button", { name: "Save integration policy" }),
  ).toBeDisabled();
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await expect
      .poll(() =>
        page
          .locator("html")
          .evaluate((element) => element.classList.contains("dark")),
      )
      .toBe(theme === "dark");
    await page.screenshot({
      path: testInfo.outputPath(`delegation-${width}-${theme}.png`),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();

  await expect(checkbox).toBeChecked();
  await checkbox.uncheck();
  await form.getByRole("button", { name: "Save integration policy" }).click();
  await expect(
    form.getByRole("button", { name: "Save integration policy" }),
  ).toBeDisabled();
  const policy = await (
    await request.get(`/api${base}/delivery/integration-policy`)
  ).json();
  expect(policy.enabled).toBe(false);
  await checkbox.check();
  await page
    .getByRole("navigation", { name: "Work", exact: true })
    .getByRole("link", {
      name: `${milestone.key} ${milestone.title}`,
      exact: true,
    })
    .click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await page
    .locator(".editor-heading")
    .getByRole("link", { name: "Settings", exact: true })
    .click();
  await expect(
    form.getByRole("button", { name: "Save integration policy" }),
  ).toBeDisabled();
  await expect(checkbox).not.toBeChecked();
});
