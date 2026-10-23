import { expect, type APIRequestContext } from "@playwright/test";
import { test } from "./support";
import { openResource } from "./shell-navigation";

async function workspace(request: APIRequestContext) {
  const response = await request.post("/api/workspaces", {
    data: {
      name: "Queue startup settings",
      ...Object.fromEntries(
        ["coordinator", "worker", "reviewer"].map((role) => [
          role,
          { harness: "codex", model: "test-model" },
        ]),
      ),
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const created = await response.json();
  const edited = await request.put(`/api/workspaces/${created.id}`, {
    data: {
      expected_revision: created.revision,
      description: "Keep this workspace description",
    },
  });
  expect(edited.ok(), await edited.text()).toBeTruthy();
  return created.id as string;
}

async function read(request: APIRequestContext, path: string) {
  const response = await request.get(path);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

for (const enabled of [false, true]) {
  test(`startup saves true and false without changing the ${enabled ? "running" : "paused"} queue`, async ({
    page,
    request,
  }) => {
    const id = await workspace(request);
    const path = `/api/workspaces/${id}`;
    // This workspace has no tasks; enabling the queue cannot launch work.
    const queue = await request.post(`${path}/queue`, {
      data: { expected_revision: 1, enabled },
    });
    expect(queue.ok(), await queue.text()).toBeTruthy();
    const before = await read(request, path);
    const workers = await read(request, `${path}/workers`);
    const occupancy = await read(request, `${path}/workers/occupancy`);
    const writes: { url: string; body: unknown }[] = [];
    page.on("request", (request) => {
      if (
        request.url().includes(path) &&
        ["PUT", "POST", "PATCH"].includes(request.method())
      )
        writes.push({ url: request.url(), body: request.postDataJSON() });
    });
    // An absent model/catalog must not disable the independent startup save.
    await page.route(`**${path}/workers`, async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        json: { ...(await response.json()), selection: null },
      });
    });
    await page.goto(`/workspaces/${id}/edit`);
    const editor = page.getByRole("region", { name: "Edit workspace" });
    const checkbox = editor.getByRole("checkbox", {
      name: "Auto-start worker queue",
    });
    await expect(checkbox).toHaveCount(0);
    await editor.getByRole("tab", { name: "Workers", exact: true }).click();
    await expect(checkbox).not.toBeChecked();
    await expect(checkbox).toHaveAccessibleDescription(
      "Start eligible work when Flowfield starts. Run or pause the current queue from the sidebar; that does not change this preference.",
    );
    const startup = editor.getByRole("form", {
      name: "Queue startup preference",
    });
    const save = startup.getByRole("button", { name: "Save changes" });
    await checkbox.focus();
    await page.keyboard.press("Space");
    await page.keyboard.press("Tab");
    await expect(save).toBeFocused();
    expect((await read(request, path)).auto_start_queue).toBe(false);
    expect(await read(request, `${path}/workers`)).toEqual(workers);
    await startup.getByRole("button", { name: "Cancel", exact: true }).click();
    await page
      .getByRole("button", { name: "Discard changes", exact: true })
      .click();
    await expect(checkbox).not.toBeChecked();
    expect(writes).toEqual([]);

    await checkbox.check();
    await editor.getByRole("tab", { name: "General", exact: true }).click();
    await expect(checkbox).toHaveCount(0);
    await editor.getByRole("tab", { name: "Workers", exact: true }).click();
    await expect(checkbox).toBeChecked();
    await openResource(page, "Projects");
    const discard = page.getByRole("alertdialog");
    await expect(discard).toBeVisible();
    await discard.getByRole("button", { name: "Keep editing" }).click();
    await expect(checkbox).toBeChecked();

    let unloads = 0;
    page.on("dialog", (dialog) => {
      if (dialog.type() === "beforeunload") unloads += 1;
    });
    // The fixture accepts the browser-owned unload prompt after observing it.
    await page.reload();
    expect(unloads).toBe(1);
    await expect(checkbox).not.toBeChecked();
    expect(writes).toEqual([]);

    for (const value of [true, false]) {
      await checkbox.setChecked(value);
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith(path) &&
          response.request().method() === "PUT",
      );
      await save.click();
      expect((await saved).ok()).toBeTruthy();
      await expect(save).toBeDisabled();
      const after = await read(request, path);
      expect(after).toMatchObject({
        name: before.name,
        description: before.description,
        auto_start_queue: value,
      });
      expect(await read(request, `${path}/workers`)).toEqual({
        ...workers,
        auto_start_queue: value,
        revision: workers.revision + (value ? 1 : 2),
      });
      expect(await read(request, `${path}/workers/occupancy`)).toEqual(
        occupancy,
      );
      await page.reload();
      await expect(checkbox).toBeChecked({ checked: value });
    }
    expect(writes).toEqual(
      [true, false].map((auto_start_queue, index) => ({
        url: `${new URL(page.url()).origin}${path}`,
        body: {
          auto_start_queue,
          expected_revision: before.revision + index,
          author: "human",
        },
      })),
    );
    const queueChanged = page.waitForResponse(
      (response) =>
        response.url().endsWith(`${path}/queue`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", {
        name: enabled ? "Pause queue" : "Run queue",
        exact: true,
      })
      .click();
    expect((await queueChanged).ok()).toBeTruthy();
    expect((await read(request, path)).auto_start_queue).toBe(false);
    expect((await read(request, `${path}/workers`)).enabled).toBe(!enabled);
    await openResource(page, "Projects");
    await expect(editor).toHaveCount(0);
  });
}

test("startup retains drafts through pending, failure and revision recovery without saving worker edits", async ({
  page,
  request,
}, testInfo) => {
  const id = await workspace(request);
  const path = `/api/workspaces/${id}`;
  await page.goto(`/workspaces/${id}/edit/workers`);
  const editor = page.getByRole("region", { name: "Edit workspace" });
  const checkbox = editor.getByRole("checkbox", {
    name: "Auto-start worker queue",
  });
  const startup = editor.getByRole("form", {
    name: "Queue startup preference",
  });
  const save = startup.getByRole("button", { name: "Save changes" });
  const cap = editor.getByLabel("Maximum parallel workers");
  await cap.fill("3");
  await checkbox.check();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**${path}`, async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    await gate;
    await route.fulfill({
      status: 503,
      json: {
        error: { message: "Startup preference temporarily unavailable" },
      },
    });
  });
  await save.click();
  await expect(save).toBeDisabled();
  await expect(checkbox).toBeDisabled();
  await expect(startup.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("startup-pending.png") });
  release();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Startup preference temporarily unavailable");
  await expect(checkbox).toBeChecked();
  await expect(save).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath("startup-error.png") });
  await page.unroute(`**${path}`);
  const old = await read(request, path);
  const competing = await request.put(path, {
    data: { expected_revision: old.revision, name: "Changed elsewhere" },
  });
  expect(competing.ok()).toBeTruthy();
  const startupConflict = page.waitForResponse(
    (response) =>
      response.url().endsWith(path) && response.request().method() === "PUT",
  );
  await save.click();
  expect((await startupConflict).status()).toBe(409);
  await expect(checkbox).toBeChecked();
  await editor
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect(checkbox).not.toBeChecked();
  await expect(cap).toHaveValue("3");
  await checkbox.check();
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith(path) && response.request().method() === "PUT",
  );
  await save.click();
  expect((await saved).ok()).toBeTruthy();
  await expect(save).toBeDisabled();
  await expect(cap).toHaveValue("3");
  await expect(
    editor.getByText(
      "Settings changed elsewhere. Load the latest settings before saving.",
    ),
  ).toBeVisible();
  expect((await read(request, `${path}/workers`)).max_parallel).toBe(5);
  await editor
    .getByRole("button", { name: "Load latest settings", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load latest settings", exact: true })
    .click();
  await expect(cap).toHaveValue("5");

  // Another client changes the workspace while General has an unsaved name.
  await editor.getByRole("tab", { name: "General", exact: true }).click();
  await editor.getByLabel("Name", { exact: true }).fill("My unsaved name");
  const current = await read(request, path);
  const external = await request.put(path, {
    data: {
      expected_revision: current.revision,
      auto_start_queue: false,
      description: "Other client description",
    },
  });
  expect(external.ok()).toBeTruthy();
  const conflict = page.waitForResponse(
    (response) =>
      response.url().endsWith(path) && response.request().method() === "PUT",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  const response = await conflict;
  expect(response.status()).toBe(409);
  expect(response.request().postDataJSON()).toEqual({
    expected_revision: current.revision,
    name: "My unsaved name",
    author: "human",
  });
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "My unsaved name",
  );
  await expect(editor.getByRole("status")).toContainText(
    "Your unsaved edits are preserved",
  );
  await page.screenshot({ path: testInfo.outputPath("general-conflict.png") });
  await editor
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect(editor.getByLabel("Description", { exact: true })).toHaveValue(
    "Other client description",
  );
  await editor.getByLabel("Name", { exact: true }).fill("Recovered name");
  const recovered = page.waitForResponse(
    (response) =>
      response.url().endsWith(path) && response.request().method() === "PUT",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  expect((await recovered).ok()).toBeTruthy();
  await page.reload();
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Recovered name",
  );
  await editor.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(checkbox).not.toBeChecked();
  expect(await read(request, `${path}/workers`)).toMatchObject({
    enabled: false,
    max_parallel: 5,
    selection: { harness: "codex", model: "test-model" },
  });
});

for (const theme of ["light", "dark"]) {
  for (const width of [1440, 390]) {
    test(`workspace General and Workers layout ${theme} ${width}`, async ({
      page,
      request,
    }, testInfo) => {
      const id = await workspace(request);
      await page.addInitScript(
        (theme) => localStorage.setItem("flowfield.theme", theme),
        theme,
      );
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/workspaces/${id}/edit`);
      const editor = page.getByRole("region", { name: "Edit workspace" });
      await editor
        .getByLabel("Name", { exact: true })
        .fill("A workspace with a long name for settings layout inspection");
      const generalSave = editor.getByRole("button", {
        name: "Save changes",
        exact: true,
      });
      await generalSave.focus();
      await expect(generalSave).toBeInViewport();
      await page.screenshot({
        path: testInfo.outputPath(`general-${theme}-${width}.png`),
      });
      await editor.getByRole("tab", { name: "Workers", exact: true }).click();
      const checkbox = editor.getByRole("checkbox", {
        name: "Auto-start worker queue",
      });
      await checkbox.focus();
      await page.keyboard.press("Space");
      await page.keyboard.press("Tab");
      const startupSave = editor
        .getByRole("form", { name: "Queue startup preference" })
        .getByRole("button", { name: "Save changes" });
      await expect(startupSave).toBeFocused();
      await expect(startupSave).toBeInViewport();
      await expect(editor.getByLabel("Maximum parallel workers")).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`workers-${theme}-${width}.png`),
      });
      const workerSave = editor.getByRole("button", {
        name: "Save worker settings",
        exact: true,
      });
      await workerSave.scrollIntoViewIfNeeded();
      await expect(workerSave).toBeInViewport();
      await page.screenshot({
        path: testInfo.outputPath(`workers-bottom-${theme}-${width}.png`),
      });
    });
  }
}
