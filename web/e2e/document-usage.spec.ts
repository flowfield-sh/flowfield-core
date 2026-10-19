import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  projectWorkspace,
  existingDirectory,
  state,
} from "./support";

test("Documents shares project defaults and opts projectless tasks into exact sources", async ({
  page,
  request,
}, testInfo) => {
  const id = "document-usage";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)), name: "Docs project" },
  });
  const workspace = await projectWorkspace(request, id);
  const path = `/api/workspaces/${workspace}/documents`;
  const doc = await (
    await request.post(path, {
      data: {
        title: "Capture conventions",
        body: "Use 1024px and return a PNG.",
      },
    })
  ).json();
  await page.goto(`/workspaces/${workspace}/documents/${doc.id}`);
  await expect(
    page.getByText("Without a project: Off", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const projectless = page.getByRole("checkbox", {
    name: "Include for tasks without a project",
  });
  await expect(projectless).not.toBeChecked();
  await projectless.check();
  await page
    .getByRole("checkbox", { name: "Docs project", exact: true })
    .check();
  await page
    .getByRole("combobox", { name: "Use for Docs project as", exact: true })
    .selectOption("reference");
  await page.getByRole("button", { name: "Save usage", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save usage", exact: true }),
  ).toBeDisabled();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/projects/${id}`)).json())
          .worker_documents,
    )
    .toContainEqual({
      id: doc.id,
      revision: null,
      purpose: "reference",
    });
  const taskResponse = await request.post(
    `/api/workspaces/${workspace}/tasks`,
    {
      data: {
        title: "Capture the homepage",
        body: "Take a 1024px screenshot and return it.",
        preparation: { completion: "report" },
      },
    },
  );
  expect(taskResponse.ok()).toBe(true);
  const task = await taskResponse.json();
  expect(
    task.publication.documents.map((item: { id: string }) => item.id).sort(),
  ).toEqual([doc.id, "guidance"].sort());
  await page
    .getByRole("combobox", {
      name: "Use for tasks without a project as",
      exact: true,
    })
    .selectOption("reference");
  const usage = await (await request.get(`${path}/${doc.id}/usage`)).json();
  await request.put(`${path}/${doc.id}/usage`, {
    data: { expected_revision: usage.revision, projectless: null },
  });
  await expect(page.getByText(/A newer revision is available/)).toBeVisible();
  await expect(
    page.getByRole("combobox", {
      name: "Use for tasks without a project as",
      exact: true,
    }),
  ).toHaveValue("reference");
  await page.getByRole("button", { name: "Save usage", exact: true }).click();
  await expect(
    page
      .locator("[data-sonner-toast]")
      .filter({ hasText: "Could not save document usage" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("document-usage-conflict.png"),
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.screenshot({
    path: testInfo.outputPath("document-usage-conflict-narrow-dark.png"),
    animations: "disabled",
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Load latest", exact: true }).click();
  await expect(projectless).not.toBeChecked();
  const preserved = await (
    await request.get(`/api/workspaces/${workspace}/tasks/${task.id}`)
  ).json();
  expect(preserved.publication.documents).toEqual(task.publication.documents);
});
