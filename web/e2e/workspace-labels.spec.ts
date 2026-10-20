import { projectWorkspace } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

test("coordinator labels update read-only task headers and preserve deletion history", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Label journey",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  const path = join(state, "labels-core");
  mkdirSync(path, { recursive: true });
  const project = await (
    await request.post(`/api/workspaces/${workspace.id}/projects`, {
      data: { path },
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
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project.id)}/tasks`,
      {
        data: {
          title: "Prepare the release notes",
          labels: ["bug", custom.id],
        },
      },
    )
  ).json();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(
    `/workspaces/${await projectWorkspace(request, project.id)}/tasks/${task.key}`,
  );
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
    await request.get(
      `/api/workspaces/${await projectWorkspace(request, project.id)}/tasks/${task.id}`,
    )
  ).json();
  expect(history.revisions[0].labels).toContain(custom.id);
  await page.goto(`/workspaces/${workspace.id}/edit`);
  await expect(
    page.getByRole("tab", { name: "Labels", exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(
    page.getByRole("list", { name: "Workspace labels" }),
  ).toContainText("New or expanded product behavior.");
  await page.getByRole("button", { name: "Add label", exact: true }).click();
  const create = page.getByRole("form", { name: "New label" });
  await expect(
    page
      .getByRole("list", { name: "Workspace labels" })
      .locator(":scope > li")
      .first()
      .getByRole("form", { name: "New label" }),
  ).toBeVisible();
  await create.getByLabel("Name", { exact: true }).fill("Customer request");
  await create
    .getByLabel("Guidance", { exact: true })
    .fill("Changes requested by a customer.");
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await expect(create.getByLabel("Name", { exact: true })).toHaveValue(
    "Customer request",
  );
  await create
    .getByRole("button", { name: "Create label", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Edit Customer request", exact: true })
    .click();
  const edit = page.getByRole("form", { name: "Edit Customer request" });
  await edit
    .getByLabel("Guidance", { exact: true })
    .fill("Requested product improvements.");
  await edit.getByRole("button", { name: "Save label", exact: true }).click();
  await expect(
    page.getByRole("list", { name: "Workspace labels" }),
  ).toContainText("Requested product improvements.");
  await page.screenshot({
    path: testInfo.outputPath("workspace-label-settings.png"),
  });
  await page
    .getByRole("button", { name: "Edit Customer request", exact: true })
    .click();
  await edit.getByRole("button", { name: "Delete label", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Delete label", exact: true })
    .click();
  await expect(
    page.getByRole("list", { name: "Workspace labels" }),
  ).not.toContainText("Customer request");
  await page.goto(
    `/workspaces/${await projectWorkspace(request, project.id)}/tasks/new`,
  );
  // Retired creation URLs identify no resource; presence reconciliation closes
  // the missing tab and returns to Inbox, without restoring a task editor.
  await expect(page).toHaveURL(
    `/workspaces/${await projectWorkspace(request, project.id)}/inbox`,
  );
  await expect(
    page.getByRole("button", { name: "Create task", exact: true }),
  ).toHaveCount(0);
});
