import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  projectWorkspace,
  existingDirectory,
  state,
  choose,
} from "./support";
import { dialogViews } from "./dialog-evidence";

async function project(
  request: Parameters<typeof fixtureProject>[0],
  id: string,
) {
  const response = await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  expect(response.ok()).toBeTruthy();
  return projectWorkspace(request, id);
}

test("discard keeps focus and drafts, expires across navigation and closes only after the decision", async ({
  page,
  request,
}, info) => {
  const workspace = await project(request, "dialog-discard");
  const root = `/workspaces/${workspace}`;
  await page.goto(root + "/documents/guidance");
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  const content = page.getByRole("textbox", { name: "Content", exact: true });
  await content.fill(
    "A draft that must survive cancellation and browser navigation.",
  );
  const cancel = page.getByRole("button", { name: "Cancel", exact: true });
  await cancel.click();
  const dialog = page.getByRole("alertdialog");
  const keep = dialog.getByRole("button", { name: "Keep editing" });
  await expect(keep).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    dialog.getByRole("button", { name: "Discard changes" }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(keep).toBeFocused();
  await dialogViews(page, info, "document-discard");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(cancel).toBeFocused();
  await expect(content).toHaveValue(/must survive/);
  await cancel.click();
  await keep.click();
  await expect(content).toHaveValue(/must survive/);
  await cancel.click();
  // Real browser history invalidates the pending decision; the retained draft survives.
  await page.goBack();
  await expect(dialog).toHaveCount(0);
  await page.goForward();
  await expect(content).toHaveValue(/must survive/);
  await expect(dialog).toHaveCount(0);
  await cancel.click();
  await dialog.getByRole("button", { name: "Discard changes" }).click();
  await expect(page).toHaveURL(root + "/documents/guidance");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expect(content).not.toHaveValue(/must survive/);
  await cancel.click();
  // The other boolean-return consumer must also wait before closing its editor.
  await page.goto(root + "/edit");
  await page.getByRole("button", { name: "Add label", exact: true }).click();
  const label = page.getByRole("form", { name: "New label" });
  await label.getByLabel("Name", { exact: true }).fill("Unsaved label");
  await label.getByRole("button", { name: "Cancel", exact: true }).click();
  await keep.click();
  await expect(label.getByLabel("Name", { exact: true })).toHaveValue(
    "Unsaved label",
  );
  await label.getByRole("button", { name: "Cancel", exact: true }).click();
  await dialog.getByRole("button", { name: "Discard changes" }).click();
  await expect(label).toHaveCount(0);
});

test("load latest preserves edits on cancellation and failure, locks pending reads and expires on revision changes", async ({
  page,
  request,
}, info) => {
  const workspace = await project(request, "dialog-reload");
  const root = `/workspaces/${workspace}`;
  const path = `/api${root}/documents/guidance`;
  await page.goto(root + "/documents/guidance/edit");
  const content = page.getByRole("textbox", { name: "Content", exact: true });
  await content.fill("Keep this unsaved draft on errors.");
  const updated = await request.put(path, {
    data: { expected_revision: 1, body: "Saved elsewhere, revision two." },
  });
  expect(updated.ok()).toBeTruthy();
  const load = page.getByRole("button", { name: "Load latest", exact: true });
  await load.click();
  const dialog = page.getByRole("alertdialog");
  await expect(
    dialog.getByRole("button", { name: "Keep editing" }),
  ).toBeFocused();
  await dialogViews(page, info, "document-load-latest");
  await page.keyboard.press("Escape");
  await expect(load).toBeFocused();
  await expect(content).toHaveValue("Keep this unsaved draft on errors.");
  await load.click();
  expect(
    (
      await request.put(path, {
        data: {
          expected_revision: 2,
          body: "Saved elsewhere, revision three.",
        },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(dialog).toHaveCount(0);
  await expect(content).toHaveValue("Keep this unsaved draft on errors.");
  let failed = true;
  let reads = 0;
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**${path}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    reads++;
    if (failed)
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "fixture_read_failure",
            message: "The latest version is temporarily unavailable.",
          },
        },
      });
    await waiting;
    return route.continue();
  });
  await load.click();
  await dialog
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  const error = page.locator('[data-sonner-toast][data-type="error"]');
  await expect(error).toHaveCount(1);
  await expect(error).toContainText("Could not load document");
  await expect(content).toHaveValue("Keep this unsaved draft on errors.");
  expect(reads).toBe(1);
  await error.getByRole("button", { name: "Close toast" }).click();
  failed = false;
  await load.click();
  await dialog
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect.poll(() => reads).toBe(2);
  await expect(content).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Save document", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeDisabled();
  release();
  await expect(content).toHaveValue("Saved elsewhere, revision three.");
  await expect(error).toHaveCount(0);
  await expect(load).toHaveCount(0);
});

test("worker and model reload decisions preserve access drafts and recover from failed reads", async ({
  page,
  request,
}, info) => {
  const workspace = await project(request, "dialog-settings");
  const base = `/api/workspaces/${workspace}`;
  await page.route(`**${base}/models*`, (route) =>
    route.fulfill({
      json: [
        {
          id: "test-model",
          name: "Test model",
          efforts: [],
          modes: [
            { id: "read-only", name: "Read only" },
            { id: "workspace-write", name: "Workspace access" },
          ],
        },
      ],
    }),
  );
  await page.goto(`/workspaces/${workspace}/edit/workers`);
  const workers = page.getByRole("region", { name: "Worker settings" });
  const cap = workers.getByLabel("Maximum parallel workers");
  await cap.fill("3");
  await choose(workers.getByLabel("Access mode"), "read-only");
  const original = await (await request.get(base + "/workers")).json();
  let changedWorkers = {
    ...original,
    revision: original.revision + 1,
    max_parallel: 2,
  };
  await page.route(`**${base}/workers`, (route) =>
    route.fulfill({ json: changedWorkers }),
  );
  const changed = await request.post(base + "/tasks", {
    data: { title: "Refresh changed settings" },
  });
  expect(changed.ok(), await changed.text()).toBeTruthy();
  const load = workers.getByRole("button", { name: "Load latest settings" });
  await load.click();
  const dialog = page.getByRole("alertdialog");
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await dialogViews(page, info, "worker-settings-reload");
  await page.keyboard.press("Escape");
  await expect(load).toBeFocused();
  await expect(cap).toHaveValue("3");
  await expect(workers.getByLabel("Access mode")).toHaveAttribute(
    "data-value",
    "read-only",
  );
  await load.click();
  changedWorkers = { ...changedWorkers, revision: changedWorkers.revision + 1 };
  const changedAgain = await request.post(base + "/tasks", {
    data: { title: "Advance the confirmation target" },
  });
  expect(changedAgain.ok(), await changedAgain.text()).toBeTruthy();
  await expect(dialog).toHaveCount(0);
  await expect(cap).toHaveValue("3");
  let fail = true;
  await page.route(`**${base}/workers`, (route) =>
    fail && route.request().method() === "GET"
      ? route.fulfill({
          status: 409,
          json: {
            error: {
              code: "fixture_failure",
              message: "Cannot load current settings.",
            },
          },
        })
      : route.fulfill({ json: changedWorkers }),
  );
  await load.click();
  await dialog.getByRole("button", { name: "Load latest settings" }).click();
  const errors = page.locator('[data-sonner-toast][data-type="error"]');
  await expect(errors).toHaveCount(1);
  await expect(errors).toContainText("Could not reload worker settings");
  await expect(cap).toHaveValue("3");
  await errors.getByRole("button", { name: "Close toast" }).click();
  fail = false;
  await load.click();
  await dialog.getByRole("button", { name: "Load latest settings" }).click();
  await expect(cap).toHaveValue("2");
  await page.goto(`/workspaces/${workspace}/tasks`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await choose(picker.getByLabel("Access mode"), "read-only");
  await page.route(`**${base}/coordinator-settings`, (route) =>
    route.request().method() === "PUT" || fail
      ? route.fulfill({
          status: 409,
          json: {
            error: {
              code: "revision_conflict",
              message: "Settings changed elsewhere.",
            },
          },
        })
      : route.continue(),
  );
  await picker.getByRole("button", { name: "Save", exact: true }).click();
  await expect(errors).toHaveCount(1);
  const reload = picker.getByRole("button", { name: "Reload", exact: true });
  await reload.click();
  await expect(dialog).toBeVisible();
  await dialogViews(page, info, "model-settings-reload");
  await page.keyboard.press("Escape");
  await expect(reload).toBeFocused();
  await expect(picker.getByLabel("Access mode")).toHaveAttribute(
    "data-value",
    "read-only",
  );
  fail = true;
  await reload.click();
  await dialog.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(errors).toHaveCount(1);
  await expect(errors).toContainText("Could not reload model settings");
  await expect(picker.getByLabel("Access mode")).toHaveAttribute(
    "data-value",
    "read-only",
  );
  fail = false;
  await reload.click();
  await dialog.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(
    picker.getByRole("button", { name: "Save", exact: true }),
  ).toBeDisabled();
});

test("unsaved drafts retain browser-owned unload protection", async ({
  page,
  request,
}) => {
  const workspace = await project(request, "dialog-unload");
  await page.goto(`/workspaces/${workspace}/documents/guidance/edit`);
  await page
    .getByRole("textbox", { name: "Content", exact: true })
    .fill("Unsaved work before reload");
  const surfaces: string[] = [];
  page.on("dialog", (dialog) => surfaces.push(dialog.type()));
  // The shared fixture accepts this native safeguard; every generic app dialog fails.
  await page.reload();
  expect(surfaces).toEqual(["beforeunload"]);
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).not.toHaveValue("Unsaved work before reload");
});
