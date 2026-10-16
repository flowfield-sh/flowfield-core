import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import {
  test,
  fixtureProject,
  projectWorkspace,
  fixtureStages,
} from "./support";

test("review defaults and task reset preserve explicit inheritance and missing setup", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "review-settings");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await fixtureProject(request, { data: { path, task_prefix: "RVS" } })
    ).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, "review-settings");
  const task = await (
    await request.post("/api/projects/review-settings/tasks", {
      data: { title: "Assess this contribution", stages: fixtureStages() },
    })
  ).json();
  await page.goto(`/workspaces/${workspace}/edit/reviewers`);
  const defaults = page.getByRole("region", { name: "Task reviewer settings" });
  await expect(
    defaults.getByLabel("Automatically assess task changes"),
  ).toBeChecked();
  await expect(
    defaults.getByText("Choose a reviewer to run automatic assessments.", {
      exact: false,
    }),
  ).toHaveCount(0);
  await defaults.getByLabel("Automatically assess task changes").uncheck();
  await defaults.getByRole("button", { name: "Save reviewer" }).click();
  await expect(
    defaults.getByRole("button", { name: "Save reviewer" }),
  ).toBeDisabled();
  const saved = await (
    await request.get(`/api/workspaces/${workspace}/reviewers/task`)
  ).json();
  expect(saved.enabled).toBe(false);
  const milestone = await (
    await request.get(`/api/workspaces/${workspace}/reviewers/milestone`)
  ).json();
  expect(milestone.enabled).toBe(true);
  await page.goto(`/projects/review-settings/tasks/${task.key}`);
  await page.getByRole("tab", { name: "Settings", exact: true }).click();
  const override = page.getByRole("region", { name: "Task reviewer settings" });
  await expect(
    override.getByLabel("Automatically assess task changes"),
  ).not.toBeChecked();
  await override.getByLabel("Automatically assess task changes").check();
  await expect(
    override.getByRole("button", { name: "Save reviewer" }),
  ).toBeDisabled();
  await override.getByLabel("Automatically assess task changes").uncheck();
  await override.getByRole("button", { name: "Save reviewer" }).click();
  await expect(
    override.getByRole("button", { name: "Reset to workspace" }),
  ).toBeEnabled();
  const resetSaved = page.waitForResponse(
    (response) =>
      response.url().includes("/reviewers/task?") &&
      response.request().method() === "PUT",
  );
  await override.getByRole("button", { name: "Reset to workspace" }).click();
  expect((await resetSaved).ok()).toBe(true);
  await expect(
    override.getByRole("button", { name: "Reset to workspace" }),
  ).toBeDisabled();
  const reset = await (
    await request.get(
      `/api/workspaces/${workspace}/reviewers/task?project=review-settings&entity=${task.id}`,
    )
  ).json();
  expect(reset.enabled).toBeNull();
  expect(reset.selection).toBeNull();
  expect(reset.effective.enabled_source).toBe("workspace");
  await page.reload();
  await page.getByRole("tab", { name: "Settings", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Task reviewer settings" })
      .getByLabel("Automatically assess task changes"),
  ).not.toBeChecked();
});
