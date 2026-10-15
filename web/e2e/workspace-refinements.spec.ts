import { openResource } from "./shell-navigation";
import { join } from "node:path";
import { expect } from "@playwright/test";
import {
  test,
  state,
  fixtureProject,
  fixtureStages,
  fixtureProgress,
  existingDirectory,
} from "./support";

test("URL-backed board views, milestone reading and document lifecycle", async ({
  page,
  request,
  context,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: { name: "Product workspace" },
    })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  const project = "refinement-core";
  await fixtureProject(request, {
    workspaceId: workspace.id,
    data: {
      path: existingDirectory(join(state, project)),
      id: project,
      name: "Core service",
      task_prefix: "RFC",
    },
  });
  const task = await (
    await request.post(`/api/projects/${project}/tasks`, {
      data: { title: "Inspect delivery", stages: fixtureStages() },
    })
  ).json();
  const milestone = await (
    await request.post(`/api${base}/milestones`, {
      data: {
        title: "Release readiness",
        body: "## Scope\nReview the release across projects.",
      },
    })
  ).json();
  await request.put(`/api/projects/${project}/tasks/${task.id}`, {
    data: { expected_revision: task.revision, milestone_id: milestone.id },
  });
  await fixtureProject(request, {
    workspaceId: workspace.id,
    data: {
      path: existingDirectory(join(state, "refinement-site")),
      id: "refinement-site",
      name: "Public website",
      task_prefix: "RFS",
    },
  });
  for (const [title, status] of [
    ["Ongoing release", "in_progress"],
    ["Delivered release", "done"],
  ] as const) {
    const group = await (
      await request.post(`/api${base}/milestones`, { data: { title } })
    ).json();
    const member = await (
      await request.post(`/api/projects/${project}/tasks`, {
        data: {
          title,
          milestone_id: group.id,
          status: status === "done" ? "backlog" : status,
          stages: fixtureStages(),
        },
      })
    ).json();
    if (status === "done") {
      const done = await fixtureProgress(request, {
        project_id: project,
        task_id: member.id,
        progress: {
          expected_revision: member.revision,
          status: "done",
          completion: "report",
        },
      });
      await request.put(`/api/projects/${project}/tasks/${member.id}`, {
        data: { expected_revision: done.revision, archived: true },
      });
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/projects`);
  const projectLink = page.getByRole("link", { name: /Core service/ });
  await expect(projectLink).toHaveAttribute(
    "href",
    `${base}?project=${project}`,
  );
  await projectLink.click();
  await expect(page).toHaveURL(`${base}?project=${project}`);
  await expect(page.getByLabel("Filter by project")).toHaveValue(project);
  await expect(
    page
      .getByRole("tablist", { name: "Open resources" })
      .locator('[role="tab"][aria-selected="true"]'),
  ).toHaveAccessibleName("Board");
  await page.screenshot({
    path: testInfo.outputPath("project-actions-desktop-light.png"),
  });
  const filters = page.locator(".board-filters select");
  await expect(filters).toHaveCount(3);
  expect(
    await filters.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("aria-label")),
    ),
  ).toEqual(["Filter by project", "Filter by milestone", "Filter by label"]);
  await page.getByLabel("Filter by label").selectOption("bug");
  await expect(page).toHaveURL(`${base}?project=${project}&label=bug`);
  expect(
    (
      await request.delete(`/api${base}/labels/bug`, {
        data: { expected_revision: 1 },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(page.getByLabel("Filter by label")).toHaveValue("");
  await expect(page).toHaveURL(`${base}?project=${project}`);
  const taskLink = page
    .locator(".task-card")
    .getByRole("link")
    .filter({ hasText: "Inspect delivery" });
  await expect(taskLink).toHaveAttribute(
    "href",
    `/projects/${project}/tasks/${task.key}`,
  );
  const other = await context.newPage();
  await other.goto(`/projects/${project}/tasks/${task.key}`);
  await expect(other).toHaveURL(`/projects/${project}/tasks/${task.key}`);
  await other.reload();
  await expect(
    other.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  const heading = other.getByRole("heading", {
    name: /RFC-1 Inspect delivery$/,
  });
  await expect(heading).toBeVisible();
  await expect(
    other
      .locator(".task-view-navigation")
      .getByRole("link", { name: "Core service" }),
  ).toHaveCount(0);
  await other.setViewportSize({ width: 1440, height: 1000 });
  await other.screenshot({
    path: testInfo.outputPath("task-identity-desktop-light.png"),
  });
  await other.setViewportSize({ width: 390, height: 844 });
  await other.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await other.reload();
  await expect(heading).toBeVisible();
  await other.screenshot({
    path: testInfo.outputPath("task-identity-narrow-dark.png"),
  });

  await other.evaluate(() => localStorage.setItem("flowfield.theme", "light"));
  await other.close();
  await page
    .getByRole("button", { name: "Project settings", exact: true })
    .click();
  await expect(page).toHaveURL(`/projects/${project}/edit`);
  await page.getByLabel("Name", { exact: true }).fill("Unsaved project");
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project}/edit/integration`);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(
    "Unsaved project",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Discard/ }).click();
  await openResource(page, "Milestones");
  await expect(
    page.getByRole("region", { name: "In progress", exact: true }),
  ).toContainText("Ongoing release");
  await expect(
    page.getByRole("region", { name: "Complete", exact: true }),
  ).toContainText("Delivered release");
  const card = page
    .getByRole("region", { name: "Planned", exact: true })
    .locator(".task-card");
  await expect(card).toContainText("Release readiness");
  await card
    .getByRole("link", { name: "Release readiness", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Milestone", exact: true }),
  ).toContainText("Review the release across projects.");
  await page.screenshot({
    path: testInfo.outputPath("milestone-pane-desktop-light.png"),
  });
  await expect(
    page.getByRole("button", { name: "Edit milestone", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await request.put(`/api${base}/milestones/${milestone.id}`, {
        data: {
          expected_revision: milestone.revision,
          title: "Release checklist",
        },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(
    page.getByRole("heading", { name: /Release checklist/ }),
  ).toBeVisible();
  await expect(page).toHaveURL(`${base}/milestones/${milestone.key}`);
  await page.getByRole("link", { name: "View tasks", exact: true }).click();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue(
    milestone.id,
  );
  await openResource(page, "Documents");
  await expect(page.getByRole("tab", { name: /^Documents/ })).toHaveText(
    "Documents 1",
  );
  await page.getByRole("link", { name: /Workspace guidance/ }).click();
  await expect(page.locator(".document-projects")).toHaveText(
    "Projects: Core service, Public website",
  );
  await page.screenshot({
    path: testInfo.outputPath("document-projects-desktop-light.png"),
  });
  await openResource(page, "Documents");
  const doc = await (
    await request.post(`/api${base}/documents`, {
      data: { title: "Architecture", body: "# Overview\nCore owns the API." },
    })
  ).json();
  const docUrl = `${base}/documents/${doc.id}`;
  await page.goto(docUrl);
  await expect(
    page.getByRole("tab", { name: "Architecture", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(/This is revision/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Delete document", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await request.delete(`/api${base}/documents/${doc.id}`, {
        data: { expected_revision: 1 },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto(docUrl);
  await expect(
    page.getByText(
      "This document was deleted. Restore a revision to use it again.",
      { exact: true },
    ),
  ).toContainText("This document was deleted.");
  await page.getByRole("link", { name: "Revision 1", exact: true }).click();
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "Architecture", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Architecture", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("document-restored-narrow-dark.png"),
  });
  await page.goto(`${base}/projects/new`);
  await expect(
    page.getByRole("region", { name: "Add a project", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("project-overlay-narrow-dark.png"),
  });
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(`${base}/projects`);
  await page.goto(`${base}/archive`);
  await expect(
    page.getByRole("button", { name: "New task", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "New milestone", exact: true }),
  ).toHaveCount(0);
  await page.locator(".archive-list .task-card-link").first().hover();
  await expect(
    page.locator(".archive-list .record-attribution").first(),
  ).toContainText("Archived by You");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("archive-card-narrow-dark.png"),
  });
  /* Legacy resource links canonicalize to project resource routes. */

  await page.goto(`${base}/projects/${project}/tasks/${task.key}`);
  await expect(page).toHaveURL(`/projects/${project}/tasks/${task.key}`);
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
});
