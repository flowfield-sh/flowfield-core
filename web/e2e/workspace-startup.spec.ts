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
    const startup = editor.getByRole("region", {
      name: "Worker settings",
      exact: true,
    });
    const save = startup.getByRole("button", { name: "Save worker settings" });
    await checkbox.focus();
    await page.keyboard.press("Space");
    await page.keyboard.press("Tab");
    await expect(save).toBeFocused();
    expect((await read(request, path)).auto_start_queue).toBe(false);
    expect(await read(request, `${path}/workers`)).toEqual(workers);
    await startup.getByRole("button", { name: "Cancel", exact: true }).click();
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
          response.url().endsWith(`${path}/workers`) &&
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
        url: `${new URL(page.url()).origin}${path}/workers`,
        body: {
          auto_start_queue,
          expected_revision: workers.revision + index,
          max_parallel: workers.max_parallel,
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
    // Queue changes refresh hidden settings. Finish intercepted reads before
    // the browser and service fixtures close their request contexts.
    await page.unrouteAll({ behavior: "wait" });
  });
}

test("one worker form preserves drafts on failure and reloads all fields on conflict", async ({
  page,
  request,
}) => {
  const id = await workspace(request);
  const path = `/api/workspaces/${id}/workers`;
  await page.goto(`/workspaces/${id}/edit/workers`);
  const editor = page.getByRole("region", {
    name: "Worker settings",
    exact: true,
  });
  const checkbox = editor.getByRole("checkbox", {
    name: "Auto-start worker queue",
  });
  const cap = editor.getByLabel("Maximum parallel workers");
  const save = editor.getByRole("button", { name: "Save worker settings" });
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
      json: { error: { message: "Settings temporarily unavailable" } },
    });
  });
  await save.click();
  await expect(checkbox).toBeDisabled();
  await expect(cap).toBeDisabled();
  release();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Settings temporarily unavailable");
  await expect(checkbox).toBeChecked();
  await expect(cap).toHaveValue("3");
  await page.unroute(`**${path}`);
  const old = await read(request, path);
  const competing = await request.put(path, {
    data: { expected_revision: old.revision, max_parallel: 4 },
  });
  expect(competing.ok()).toBeTruthy();
  // A background receipt makes the shared draft stale rather than partly saving it.
  await expect(
    editor.getByText(
      "Settings changed elsewhere. Load the latest settings before saving.",
    ),
  ).toBeVisible();
  await expect(save).toBeDisabled();
  await editor
    .getByRole("button", { name: "Load latest settings", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load latest settings", exact: true })
    .click();
  await expect(cap).toHaveValue("4");
  await expect(checkbox).not.toBeChecked();
  await cap.fill("3");
  await checkbox.check();
  await save.click();
  await expect(save).toBeDisabled();
  expect(await read(request, path)).toMatchObject({
    max_parallel: 3,
    auto_start_queue: true,
    enabled: false,
    selection: old.selection,
  });
  await page.reload();
  await expect(cap).toHaveValue("3");
  await expect(checkbox).toBeChecked();
  // General owns only its fields and cannot revert a separately changed startup flag.
  const workspaceEditor = page.getByRole("region", { name: "Edit workspace" });
  await workspaceEditor
    .getByRole("tab", { name: "General", exact: true })
    .click();
  await workspaceEditor
    .getByLabel("Name", { exact: true })
    .fill("Renamed workspace");
  await workspaceEditor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(
    workspaceEditor.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeDisabled();
  expect((await read(request, path)).auto_start_queue).toBe(true);
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
      await page.route("**/api/workspaces/*/models*", (route) =>
        route.fulfill({
          json: [
            {
              id: "test-model",
              name: "Test model",
              efforts: [],
              modes: [],
              fast: true,
            },
          ],
        }),
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
      const workerSettings = editor.getByRole("region", {
        name: "Worker settings",
        exact: true,
      });
      await expect(
        workerSettings.getByRole("checkbox", {
          name: "Auto-start worker queue",
        }),
      ).toHaveCount(1);
      const fast = workerSettings.getByRole("checkbox", { name: "Fast mode" });
      await expect(fast).toBeVisible();
      // The queue controls belong below the shared divider after model options.
      const order = await workerSettings.evaluate((section) => {
        const fast = section.querySelector('input[type="checkbox"]')!;
        const divider = section.querySelector('[data-slot="separator"]')!;
        const capacity = section.querySelector('input[type="number"]')!;
        const startup = section.querySelectorAll('input[type="checkbox"]')[1]!;
        const save = section.querySelector(
          'button[aria-label="Save worker settings"]',
        )!;
        const follows = (a: Element, b: Element) =>
          !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
        return (
          follows(fast, divider) &&
          follows(divider, capacity) &&
          follows(capacity, startup) &&
          follows(startup, save) &&
          section.querySelectorAll("form").length === 1
        );
      });
      expect(order).toBe(true);
      await checkbox.focus();
      await page.keyboard.press("Space");
      await page.keyboard.press("Tab");
      const startupSave = editor
        .getByRole("region", { name: "Worker settings", exact: true })
        .getByRole("button", { name: "Save worker settings" });
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
