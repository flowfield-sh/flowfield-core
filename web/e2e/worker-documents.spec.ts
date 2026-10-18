import { projectWorkspace } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

test("project defaults prepare exact task documents and preserve revision links", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Worker guidance",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  const path = join(state, "documented-core");
  mkdirSync(path, { recursive: true });
  const project = await (
    await request.post(`/api/workspaces/${workspace.id}/projects`, {
      data: { path },
    })
  ).json();
  const doc = await (
    await request.post(`/api/workspaces/${workspace.id}/documents`, {
      data: {
        title: "Core conventions",
        body: "Keep the public API stable. 🦉",
      },
    })
  ).json();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/projects/${project.id}/edit`);
  await page
    .locator("summary")
    .filter({ hasText: /^Worker documents/ })
    .click();
  await page
    .getByRole("checkbox", { name: "Core conventions", exact: true })
    .check();
  await page
    .getByRole("combobox", { name: "Use Core conventions as" })
    .selectOption("reference");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeDisabled();
  await page.reload();
  await page
    .locator("summary")
    .filter({ hasText: /^Worker documents/ })
    .click();
  await expect(
    page.getByRole("checkbox", { name: "Core conventions", exact: true }),
  ).toBeChecked();
  await expect(
    page.getByRole("combobox", { name: "Use Core conventions as" }),
  ).toHaveValue("reference");
  await expect(
    page.getByRole("checkbox", { name: "Workspace guidance", exact: true }),
  ).toBeChecked();
  await page.screenshot({
    path: testInfo.outputPath("worker-documents-desktop-light.png"),
    animations: "disabled",
  });
  await expect(
    page.getByRole("checkbox", { name: "Workspace guidance", exact: true }),
  ).toBeDisabled();
  // Preserve a dirty selection when the project is edited elsewhere.
  await page
    .getByRole("checkbox", { name: "Core conventions", exact: true })
    .uncheck();
  const saved = await (await request.get(`/api/projects/${project.id}`)).json();
  await request.put(`/api/projects/${project.id}`, {
    data: {
      expected_revision: saved.revision,
      description: "Changed elsewhere",
    },
  });
  await expect(page.getByText(/A newer revision is available/)).toBeVisible();
  await expect(
    page.getByRole("checkbox", { name: "Workspace guidance", exact: true }),
  ).toBeChecked();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  const taskResponse = await request.post(
    `/api/workspaces/${await projectWorkspace(request, project.id)}/tasks`,
    {
      data: {
        project_id: project.id,
        title: "Use documented conventions",
        body: "Review the public API",

        preparation: { completion: "report" },
      },
    },
  );
  expect(taskResponse.ok(), await taskResponse.text()).toBeTruthy();
  const task = await taskResponse.json();
  expect(task.publication.documents).toHaveLength(2);
  expect(
    task.publication.documents.find(
      (item: { id: string }) => item.id === doc.id,
    ).purpose,
  ).toBe("reference");
  await request.put(`/api/workspaces/${workspace.id}/documents/${doc.id}`, {
    data: { expected_revision: 1, body: "Updated after preparation" },
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(request, project.id)}/tasks/${task.key}`,
  );
  await expect(
    page.getByText("Newer source revision available", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "2 documents", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(/bytes of source text/)).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await page.screenshot({
    path: testInfo.outputPath("worker-documents-narrow-dark.png"),
    animations: "disabled",
  });
  // Removing the header control preserves frozen sources and their readable revision URLs.
  const frozen = task.publication.documents.find(
    (item: { id: string }) => item.id === doc.id,
  );
  await page.goto(
    `/workspaces/${workspace.id}/documents/${doc.id}/revisions/${frozen.revision}`,
  );
  await expect(page).toHaveURL(new RegExp(`/documents/${doc.id}/revisions/1$`));
  await expect(
    page.getByText("Keep the public API stable. 🦉", { exact: true }),
  ).toBeVisible();
  await page.goto(`/workspaces/${workspace.id}/tasks`);
  await expect(
    page.getByText("Newer source revision available", { exact: true }),
  ).toBeVisible();
  await page
    .getByTestId("work")
    .getByRole("link", { name: /Use documented conventions/ })
    .click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${task.key}$`));
});
