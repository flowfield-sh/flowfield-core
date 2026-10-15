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
  await page.getByText("Task integration", { exact: true }).click();
  const form = page.getByRole("region", { name: "Task integration settings" });
  const checkbox = form.getByRole("checkbox");
  await expect(checkbox).not.toBeChecked();
  await expect(form).toContainText("flowfield/reviewed-feature");
  await checkbox.check();
  await form.getByRole("button", { name: "Save integration policy" }).click();
  await expect(form).toContainText("Policy revision 2");
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
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
  await page.reload();
  await page.getByText("Task integration", { exact: true }).click();
  await expect(checkbox).toBeChecked();
  await checkbox.uncheck();
  await form.getByRole("button", { name: "Save integration policy" }).click();
  await expect(form).toContainText("Policy revision 3");
  const policy = await (
    await request.get(`/api${base}/delivery/integration-policy`)
  ).json();
  expect(policy.enabled).toBe(false);
});
