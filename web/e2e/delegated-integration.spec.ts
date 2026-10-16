import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import {
  test,
  state,
  fixtureProject,
  projectWorkspace,
  existingDirectory,
} from "./support";

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
  await page.getByRole("tab", { name: "Settings", exact: true }).click();
  await expect(page).toHaveURL(/tab=settings$/);
  await expect(
    page.getByRole("heading", { name: "Integration", exact: true }),
  ).toBeVisible();
  const form = page.getByRole("region", { name: "Task integration settings" });
  const checkbox = form.getByRole("checkbox");
  await expect(checkbox).not.toBeChecked();
  await expect(form).toContainText("flowfield/reviewed-feature");
  await checkbox.check();
  await expect(
    page.getByRole("heading", { name: "Reviewer", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Policy revision|Verification plan/)).toHaveCount(
    0,
  );
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
  await page.getByRole("tab", { name: "Settings", exact: true }).click();
  await expect(
    form.getByRole("button", { name: "Save integration policy" }),
  ).toBeDisabled();
  await expect(checkbox).not.toBeChecked();
});

test("workspace integration defaults inherit while milestone overrides remain explicit", async ({
  page,
  request,
}) => {
  const project = "default-integration";
  await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
      task_prefix: "DFI",
    },
  });
  const repo = join(state, project);
  execFileSync("git", ["init", "-b", "main"], { cwd: repo });
  execFileSync(
    "git",
    [
      "-c",
      "user.name=Fixture",
      "-c",
      "user.email=fixture@example.test",
      "commit",
      "--allow-empty",
      "-m",
      "Initial",
    ],
    { cwd: repo },
  );
  const workspace = await projectWorkspace(request, project);
  expect(
    (
      await request.put(`/api/projects/${project}/integration`, {
        data: { expected_revision: 1, target_branch: "main", checks: ["true"] },
      })
    ).ok(),
  ).toBe(true);
  const milestone = await (
    await request.post(`/api/workspaces/${workspace}/milestones`, {
      data: { title: "Inherited delivery", acceptance_criteria: "Ready" },
    })
  ).json();
  const base = `/workspaces/${workspace}/milestones/${milestone.id}`;
  expect(
    (
      await request.put(`/api${base}/delivery`, {
        data: { expected_revision: 1, project_id: project },
      })
    ).ok(),
  ).toBe(true);
  await page.goto(`/workspaces/${workspace}/edit/integration`);
  const form = page.getByRole("region", { name: "Task integration settings" });
  const enabled = form.getByRole("checkbox");
  await expect(enabled).not.toBeChecked();
  await enabled.check();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(enabled).toBeChecked();
  await form.getByRole("button", { name: "Save integration policy" }).click();
  await expect(
    form.getByRole("button", { name: "Save integration policy" }),
  ).toBeDisabled();
  await page.goto(`${base}?tab=settings`);
  await expect(enabled).toBeChecked();
  await expect(form).toContainText("Uses workspace default");
  await enabled.uncheck();
  await form.getByRole("button", { name: "Save integration policy" }).click();
  await expect(form).toContainText("Milestone override");
  await page.reload();
  await expect(enabled).not.toBeChecked();
  await form.getByRole("button", { name: "Reset to workspace" }).click();
  await expect(enabled).toBeChecked();
  const defaults = await (
    await request.get(`/api/workspaces/${workspace}/integration-policy`)
  ).json();
  await request.put(`/api/workspaces/${workspace}/integration-policy`, {
    data: { expected_revision: defaults.revision, enabled: false },
  });
  await expect(enabled).not.toBeChecked();
  await expect(form).toContainText("Uses workspace default");
  await enabled.check();
  await request.put(`/api/workspaces/${workspace}/integration-policy`, {
    data: { expected_revision: defaults.revision + 1, enabled: true },
  });
  await expect(form.getByRole("status")).toContainText(
    "unsaved edits are preserved",
  );
  await expect(enabled).toBeChecked();
  await expect(
    form.getByRole("button", { name: "Save integration policy" }),
  ).toBeDisabled();
  page.once("dialog", (dialog) => dialog.accept());
  await form.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(form).toContainText("Uses workspace default");
  await expect(form.getByRole("status")).toHaveCount(0);
});
