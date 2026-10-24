import { expect } from "@playwright/test";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
} from "./support";
import { join } from "node:path";

test("permanent deletion confirms exact task and removes it from the board", async ({
  page,
  request,
}, testInfo) => {
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, "delete-planning")) },
  });
  const workspace = await projectWorkspace(request, "delete-planning");
  const base = `/workspaces/${workspace}`;
  const response = await request.post(`/api${base}/tasks`, {
    data: {
      title: "Remove abandoned experiment",
      project_id: "delete-planning",
    },
  });
  expect(response.ok()).toBe(true);
  const task = await response.json();
  await page.goto(`${base}/tasks/${task.key}`);
  await expect(page.locator(".editor-heading h2")).toHaveText("Task");
  await expect(page.locator(".task-summary h3")).toContainText(task.title);
  await expect(page.locator(".editor-heading .detail-metadata")).toHaveCount(0);
  await expect(page.locator(".task-summary .detail-metadata")).toContainText(
    "Project:",
  );
  await expect(page.locator(".task-summary")).toHaveCSS("gap", "8px");
  const remove = page.getByRole("button", { name: "Delete task", exact: true });
  await expect(remove).toBeEnabled();
  await remove.hover();
  await expect(page.getByRole("tooltip")).toHaveText("Delete task");
  await page.keyboard.press("Escape");
  await remove.focus();
  await expect(page.getByRole("tooltip")).toHaveText("Delete task");
  await page.keyboard.press("Escape");
  await remove.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await expect(dialog).toContainText("including local edits");
  for (const colorScheme of ["light", "dark"] as const) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme });
      await page.screenshot({
        path: testInfo.outputPath(`delete-${colorScheme}-${width}.png`),
      });
    }
  }
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect((await request.get(`/api${base}/tasks/${task.id}`)).ok()).toBe(true);
  await remove.click();
  await dialog
    .getByRole("button", { name: "Delete task", exact: true })
    .click();
  await expect
    .poll(async () =>
      (await request.get(`/api${base}/tasks/${task.id}`)).status(),
    )
    .toBe(404);
  await page.goto(`${base}/tasks`);
  await expect(
    page.getByRole("link", { name: task.title, exact: true }),
  ).toHaveCount(0);
  const replacement = await (
    await request.post(`/api${base}/tasks`, {
      data: { title: "New experiment" },
    })
  ).json();
  expect(replacement.key).not.toBe(task.key);
});
