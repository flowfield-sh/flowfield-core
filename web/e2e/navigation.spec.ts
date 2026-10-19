import { openResource } from "./shell-navigation";
import {
  fixtureCliProject,
  projectWorkspace,
  fixtureProject,
  choose,
  test,
  existingDirectory,
  state,
  cli,
  closeOverlay,
  connectMcp,
} from "./support";
import { join } from "node:path";

import { expect } from "@playwright/test";

test("task keys link to persistent selections with history and draft protection", async ({
  page,
  request,
}) => {
  fixtureCliProject([
    existingDirectory(join(state, "navigation")),
    "--name",
    "Navigation trial",
  ]);
  fixtureCliProject([
    existingDirectory(join(state, "nav-other")),
    "--name",
    "Other navigation",
  ]);
  const call = await connectMcp(request);
  const workspace = await projectWorkspace(page.request, "navigation");
  const otherWorkspace = await projectWorkspace(page.request, "nav-other");
  const one = await call("create_task", {
    workspace_id: await projectWorkspace(page.request, "navigation"),
    task: {
      project_id: "navigation",
      title: "Prepare the export",
      status: "up_next",
    },
  });
  const two = await call("create_task", {
    workspace_id: await projectWorkspace(page.request, "navigation"),
    task: {
      project_id: "navigation",
      title: "Download the export",
      status: "up_next",
      dependencies: [one.key],
    },
  });
  const old = await call("create_task", {
    workspace_id: await projectWorkspace(page.request, "navigation"),
    task: { project_id: "navigation", title: "Earlier experiment" },
  });
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "navigation"),
    task_id: old.key,
    changes: { expected_revision: 1, archived: true },
  });
  expect([one.key, two.key, old.key]).toEqual(["T-1", "T-2", "T-3"]);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "navigation")}/tasks/${two.key}`,
  );
  const waiting = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    waiting.getByRole("heading", { name: "Waiting on", exact: true }),
  ).toBeVisible();
  await expect(
    waiting.getByRole("link", { name: one.key, exact: true }).first(),
  ).toBeVisible();
  await page.goto(`/workspaces/${workspace}/tasks`);
  const blocker = page.getByRole("link", { name: "T-1", exact: true });
  await blocker.hover();
  await expect(page.getByRole("tooltip")).toHaveText("Prepare the export");
  await page.keyboard.press("Escape");
  await expect(blocker).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(page.request, "navigation")}/tasks/T-1`,
  );
  await expect(
    page.getByText("Completion criteria set", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Add completion criteria", { exact: true }),
  ).toHaveCount(0);
  await blocker.click();
  await expect(page).toHaveURL(/tasks\/T-1$/);
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: /Prepare the export$/ }),
  ).toContainText("Prepare the export");
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: /Prepare the export$/ }),
  ).toBeVisible();
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Unsaved title");
  await page.goBack();
  await expect(page).toHaveURL(`/workspaces/${workspace}/tasks`);
  await page.goForward();
  await expect(page).toHaveURL(/tasks\/T-1$/);
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Unsaved title");
  await page.goBack();
  await expect(page).toHaveURL(`/workspaces/${workspace}/tasks`);
  await expect(editor).toHaveCount(0);
  await page.goForward();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: /Prepare the export$/ }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", {
      name: /T-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  const prerequisite = editor.getByRole("link", { name: "T-1", exact: true });
  await expect(editor.getByLabel("Current task definition")).toBeVisible();
  await prerequisite.click();
  await expect(page).toHaveURL(/tasks\/T-1$/);
  await page.goBack();
  await expect(page).toHaveURL(/tasks\/T-2(?:\/dependencies)?$/);
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: /Download the export$/ }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .getByLabel("Workspace", { exact: true })
    .selectOption(otherWorkspace);
  await expect(page).toHaveURL(`/workspaces/${otherWorkspace}/inbox`);
  await page.goBack();
  await expect(page).toHaveURL(`/workspaces/${workspace}/tasks`);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", {
      name: /T-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: /Download the export$/ }),
  ).toBeVisible();
  const renamed = await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "navigation"),
    task_id: one.key,
    changes: {
      expected_revision: 1,
      title: "Prepare the corrected export",
      labels: ["bug"],
    },
  });
  expect(renamed.key).toBe("T-1");
  await prerequisite.hover();
  await expect(page.getByRole("tooltip")).toHaveText(
    "Prepare the corrected export",
  );
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "navigation")}/tasks/T-3`,
  );
  await expect(page).toHaveURL(/tasks\/T-3$/);
  await expect(
    page.getByRole("region", { name: "Archived tasks", includeHidden: true }),
  ).toBeHidden();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: /Earlier experiment$/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    editor.getByRole("button", { name: "Restore", exact: true }),
  ).toHaveCount(0);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "navigation")}/tasks/T-999`,
  );
  await expect(
    page.getByRole("heading", { name: "Task not found", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "New task", exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("closing entity panes returns through history without duplicate collections", async ({
  page,
  request,
}) => {
  const id = "history-roundtrip";
  expect(
    (
      await fixtureProject(request, {
        data: {
          id,
          path: existingDirectory(join(state, id)),
        },
      })
    ).ok(),
  ).toBeTruthy();
  await request.post(
    `/api/workspaces/${await projectWorkspace(page.request, id)}/tasks`,
    {
      data: { id: "one", title: "History task" },
    },
  );
  const call = await connectMcp(request);
  await call("ask_question", {
    workspace_id: await projectWorkspace(page.request, id),
    question: {
      id: "scope",
      task_id: "T-1",
      question: "Which scope?",
      context: "Choose the scope",
      recommendation: "Small",
    },
  });
  const workspace = await projectWorkspace(page.request, id);
  const board = `/workspaces/${workspace}/tasks`;
  const inbox = `/workspaces/${workspace}/inbox`;
  await page.goto(board);
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await page
    .getByRole("link", { name: "Open request in work tab", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Back", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(/requests\/question\//);
  await page.goBack();
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(board);
  await page.goForward();
  await expect(page).toHaveURL(inbox);
  await page.goForward();
  await expect(
    page.getByRole("region", { name: "Request", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Request", exact: true }),
  ).toBeVisible();
  const answer = page.getByRole("textbox", {
    name: "Your answer",
    exact: true,
  });
  await expect(answer).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(answer).not.toBeFocused();
  await expect(
    page.getByRole("region", { name: "Request", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Back", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await expect(page).toHaveURL(inbox);
  await openResource(page, "Tasks Board");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /T-1/ })
    .click();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back)$/,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(board);
  // A fresh browser entry has no synthetic history; use the sidebar to open Board.
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, id)}/tasks/T-1`,
  );
  await expect(
    page.getByRole("button", { name: "Back", exact: true }),
  ).toBeDisabled();
  await openResource(page, "Tasks Board");
  await expect(page).toHaveURL(board);
});

test("entity identity and drafts persist across task and workspace tabs", async ({
  page,
  request,
}) => {
  const project = "identity-layout";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, project)) },
  });
  const workspace = await projectWorkspace(page.request, project);

  await request.post(
    `/api/workspaces/${await projectWorkspace(page.request, project)}/tasks`,
    {
      data: {
        id: "first",
        title: "First task",
      },
    },
  );
  await page.goto("/");
  const sidebar = page.locator('[data-slot="sidebar"]');
  const logo = sidebar.getByRole("link", { name: "Home", exact: true });
  const projectLink = sidebar.getByLabel("Workspace", { exact: true });
  await expect(projectLink).toBeVisible();
  await expect(
    sidebar.getByRole("button", { name: "Tasks Board", exact: true }),
  ).toHaveCount(0);

  await expect(logo).toHaveAttribute("href", "/");

  await projectLink.selectOption(workspace);
  await expect(page).toHaveURL(`/workspaces/${workspace}/inbox`);
  await openResource(page, "Tasks Board");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /T-1/ })
    .click();
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const title = editor.getByRole("heading", {
    name: /T-1 First task$/,
  });

  await page.getByRole("tab", { name: "Task", exact: true }).click();
  await editor.locator('[data-message-id="definition:1"] summary').click();
  await expect(title).toBeVisible();
  await expect(
    editor.getByRole("tab", { name: "Task", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    editor.getByText("Loading execution…", { exact: true }),
  ).toHaveCount(0);
  await expect(editor.getByLabel("Add prerequisite")).toHaveCount(0);
  await expect(
    editor.getByRole("button", { name: "Edit M-1", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "New task", exact: true }),
  ).toHaveCount(0);
  let releaseSettings!: () => void;
  const settingsReady = new Promise<void>((resolve) => {
    releaseSettings = resolve;
  });
  await page.route(`**/api/workspaces/${workspace}/workers`, async (route) => {
    await settingsReady;
    await route.continue();
  });
  await openResource(page, "Settings");
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(page).toHaveURL(
    new RegExp(`/workspaces/${workspace}/edit/workers$`),
  );
  await expect(page.getByLabel("Maximum parallel workers")).toBeDisabled();
  releaseSettings();
  await page.getByLabel("Maximum parallel workers").fill("3");
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Draft workspace name");
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(page.getByLabel("Maximum parallel workers")).toHaveValue("3");
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(
    "Draft workspace name",
  );
  // Switch to mobile, then deliberately discard the drafts on refresh.
  await page.setViewportSize({ width: 390, height: 844 });

  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "General", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(project);
});

test.describe("relative timestamps", () => {
  test.use({ locale: "en-US", timezoneId: "America/Denver" });

  test("timestamps refresh and expose the exact local time on hover and focus", async ({
    page,
  }) => {
    const project = "relative-times";
    fixtureCliProject([existingDirectory(join(state, project))]);
    const task = cli([
      "task",
      "create",
      "--title",
      "Timestamp evidence",
      "--workspace",
      await projectWorkspace(page.request, project),
      "--body",
      "Keep timestamp evidence accessible.",
    ]);
    const created = Date.parse(task.updated_at);
    await page.clock.install({ time: new Date(created) });
    await page.clock.pauseAt(new Date(created + 10000));
    await page.goto(
      `/workspaces/${await projectWorkspace(page.request, project)}/tasks/T-1/history`,
    );
    const card = page.locator(".task-activity-item").first();
    const time = card.locator("time").first();
    await expect(time).toHaveText("just now");
    await expect(time).toHaveAttribute("datetime", task.updated_at);
    const exact = new Intl.DateTimeFormat("en-US", {
      dateStyle: "full",
      timeStyle: "long",
      timeZone: "America/Denver",
    }).format(new Date(created));
    // Initial feed layout follows its last entry; settle its animation frames before hover.
    await expect(card).toBeVisible();
    await page.clock.runFor(1000);
    await time.hover();
    await page.clock.runFor(100);
    await expect(page.getByRole("tooltip")).toHaveText(exact);
    await page.mouse.move(0, 0);
    await page.clock.runFor(100);
    await time.focus();
    await page.clock.runFor(100);
    await expect(page.getByRole("tooltip")).toHaveText(exact);
    // Time advances without a data refresh; the date behind the label stays fixed.
    await page.clock.fastForward(290000);
    await expect(time).toHaveText("5 minutes ago");
    await expect(page.getByRole("tooltip")).toHaveText(exact);
    const detail = page.getByRole("region", {
      name: "Task details",
      exact: true,
    });
    const detailTime = detail.locator("time").first();
    await expect(detailTime).toHaveText("5 minutes ago");
    await detailTime.focus();
    await expect(page.getByRole("tooltip")).toHaveText(exact);
  });
});

test("workspace selector names remain accessible and idle input opens deliberately", async ({
  page,
  request,
}) => {
  const name = "A carefully tended collection of pocket gardens";
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "sidebar-names")),
      name,
    },
  });
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, "short-name")), name: "Short" },
  });
  const workspace = await projectWorkspace(page.request, "sidebar-names");
  const shortWorkspace = await projectWorkspace(page.request, "short-name");
  const path = `/api/workspaces/${await projectWorkspace(page.request, "sidebar-names")}`;
  await request.post(path + "/tasks", {
    data: {
      id: "idle",
      title: "Read the current agreement",
      body: "Useful intent",
    },
  });
  const question = await request.post(path + "/questions", {
    data: {
      question: "Which outcome matters most?",
      context: "Choose the next project priority.",
      recommendation: "Discuss the priorities with the coordinator.",
      author: "agent",
    },
  });
  expect(question.ok()).toBe(true);
  // Other journeys adopt projects concurrently. Scope the selector's records
  // so its keyboard choices stay deterministic.
  await page.route("**/api/workspaces", async (route) => {
    const response = await route.fetch();
    const projects = await response.json();
    await route.fulfill({
      json: projects.filter((project: { id: string }) =>
        [workspace, shortWorkspace].includes(project.id),
      ),
    });
  });
  await page.goto(`/workspaces/${workspace}/tasks`);
  const settings = page.getByRole("dialog", {
    name: "Coordinator model settings",
    exact: true,
  });
  await expect(settings).not.toBeVisible();
  await expect(settings).not.toBeVisible();
  const selector = page.getByLabel("Workspace", { exact: true });
  await expect(selector.locator("option:checked")).toContainText(name);
  await selector.selectOption(shortWorkspace);
  await expect(selector.locator("option:checked")).toHaveText("Short");
  await page.setViewportSize({ width: 650, height: 844 });
  await page
    .getByRole("button", { name: "Open workspaces", exact: true })
    .click();
  await expect(
    selector.locator('option[value="' + workspace + '"]'),
  ).toHaveText(name + " (1)");
  await selector.selectOption(workspace);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: /^Inbox 1$/ })).toBeVisible();
  await page
    .getByRole("button", { name: "Open workspaces", exact: true })
    .click();
  await page.getByRole("link", { name: "Tasks Board", exact: true }).click();
  await expect(
    page.getByRole("link", { name: /item needs your attention/ }),
  ).toHaveCount(0);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Read the current agreement/ })
    .click();
  await expect(page.getByLabel("Current task definition")).toContainText(
    "Definition",
  );
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("textbox"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Latest", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Ask worker", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toBeVisible();
  expect(
    (await (await request.get(path + "/tasks/idle/thread")).json()).items.some(
      (item: { kind: string }) => item.kind === "reply",
    ),
  ).toBe(false);
});

test("workspace navigation, mobile board and appearance work beside the coordinator", async ({
  page,
  request,
}, testInfo) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "workspace-frame")),
      name: "Workspace frame",
    },
  });
  await request.post(
    `/api/workspaces/${await projectWorkspace(page.request, "workspace-frame")}/tasks`,
    {
      data: { title: "Review the workspace layout" },
    },
  );
  const workspace = await projectWorkspace(page.request, "workspace-frame");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "workspace-frame")}/tasks`,
  );
  await expect(page.locator("html")).toHaveClass("dark");
  await expect(page.getByLabel("Workspace", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("separator", { name: "Resize coordinator and work" }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Coordinator", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Coordinator conversation", exact: true }),
  ).toBeVisible();
  const modelSettings = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await expect(modelSettings).not.toBeVisible();
  const divider = page.getByRole("separator", {
    name: "Resize coordinator and work",
  });
  const pane = page.locator(".workspace-pane").first();
  async function dragDivider(deltas: number[]) {
    await divider.hover();
    const start = (await pane.boundingBox())!.width;
    const handle = (await divider.boundingBox())!;
    const x = handle.x + handle.width / 2;
    const y = handle.y + handle.height / 2;
    await page.mouse.down();
    for (const delta of deltas) {
      await page.mouse.move(x + delta, y);
      // Wait for each layout update with the button held: a re-registered panel
      // can pass a single movement while dropping every later drag event.
      await expect
        .poll(async () =>
          Math.abs((await pane.boundingBox())!.width - start - delta),
        )
        .toBeLessThan(2);
    }
    await page.mouse.up();
  }
  await dragDivider([1, 40, 120, 60, -40]);
  await dragDivider([20, 50]);
  const resized = (await pane.boundingBox())!.width;
  await page.reload();
  await expect(pane).toBeVisible();
  await expect(modelSettings).not.toBeVisible();
  await expect
    .poll(async () => {
      const box = await pane.boundingBox();
      return box ? Math.abs(box.width - resized) : Infinity;
    })
    .toBeLessThan(2);
  const before = await pane.boundingBox();
  await divider.focus();
  await page.keyboard.press("ArrowRight");
  await expect
    .poll(async () => (await pane.boundingBox())!.width)
    .toBeGreaterThan(before!.width);
  const projectTabs = page.locator(".workspace-tab-strip");
  expect(
    await projectTabs.evaluate(
      (node) => node.scrollHeight === node.clientHeight,
    ),
  ).toBe(true);
  const card = page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Review the workspace layout/ });
  await card.click();
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail).toBeVisible();
  await expect(
    page.getByRole("tablist", { name: "Open resources" }),
  ).toBeVisible();
  const back = detail.getByRole("button", { name: "Back" });
  const heading = detail.locator(".entity-pane-title h2");
  expect((await back.boundingBox())!.x).toBeLessThan(
    (await heading.boundingBox())!.x,
  );
  const comparison = detail.getByRole("link", { name: "Open in work tab" });
  const titleBox = (await heading.boundingBox())!;
  expect((await comparison.boundingBox())!.x).toBeGreaterThanOrEqual(
    titleBox.x + titleBox.width,
  );
  await page.screenshot({
    path: testInfo.outputPath("task-navigation-desktop.png"),
  });
  await openResource(page, "Tasks Board");
  await expect(detail).not.toBeVisible();
  await expect(card).toBeVisible();
  await page.goBack();
  await expect(detail).toBeVisible();
  await back.click();
  await expect(card).toBeFocused();
  // History reopening must resolve the task entry even if the Board tab still
  // owns focus, and an older restoration frame must not steal from the new pane.
  for (let visit = 0; visit < 2; visit++) {
    await card.click();
    await expect(detail).toBeVisible();
    await openResource(page, "Tasks Board");
    await expect(detail).not.toBeVisible();
    await page.goBack();
    await expect(detail).toBeVisible();
    await back.click();
    await expect(card).toBeFocused();
  }
  await expect(
    page.getByRole("button", { name: /Collapse projects|Expand projects/ }),
  ).toHaveCount(0);
  await page
    .getByRole("link", { name: "Flowfield settings", exact: true })
    .click();
  await page.getByRole("tab", { name: "Appearance", exact: true }).click();
  await choose(page.getByLabel("Theme", { exact: true }), "light");
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
  await openResource(page, "Tasks Board");
  await expect(page.locator("html")).not.toHaveClass("dark");
  expect(
    await page
      .locator(".column")
      .first()
      .evaluate((node) => getComputedStyle(node).backgroundColor),
  ).not.toBe(
    await page
      .locator(".task-card")
      .first()
      .evaluate((node) => getComputedStyle(node).backgroundColor),
  );
  await page.reload();
  await expect(page.locator("html")).not.toHaveClass("dark");
  await expect(
    page.getByRole("button", { name: "Expand projects", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("link", { name: "Flowfield settings", exact: true })
    .click();
  await page.getByRole("tab", { name: "Appearance", exact: true }).click();
  await choose(page.getByLabel("Theme", { exact: true }), "system");
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
  await openResource(page, "Tasks Board");
  await expect(page.locator("html")).toHaveClass("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.getByRole("tab", { name: /^Tasks/ }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: /^Inbox/ })).toBeFocused();
  await expect(page).toHaveURL(`/workspaces/${workspace}/tasks`);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inbox$/);
  await page.goBack();
  await openResource(page, "Files");
  await expect(page).toHaveURL(/\/files$/);
  await page.goBack();
  await expect(page.getByRole("tab", { name: /^Tasks/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Open workspaces", exact: true })
    .click();
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
  await openResource(page, "Tasks Board");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const board = page.locator(".board");
  expect(
    await board.evaluate((node) => node.scrollWidth > node.clientWidth),
  ).toBe(true);
  expect(
    await page
      .locator(".column")
      .first()
      .evaluate((node) => node.getBoundingClientRect().width),
  ).toBeGreaterThanOrEqual(280);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Review the workspace layout/ })
    .click();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tablist", { name: "Open resources" }),
  ).toBeVisible();
  expect(
    await projectTabs.evaluate(
      (node) => node.scrollHeight === node.clientHeight,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("task-navigation-mobile.png"),
  });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tab", { name: /^Tasks/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("long task tooltips never expand the workspace scroll owners", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "tooltip-overflow")),
    },
  });
  await request.post(
    `/api/workspaces/${await projectWorkspace(page.request, "tooltip-overflow")}/tasks`,
    {
      data: {
        title: "Long definition",
        body: Array.from(
          { length: 70 },
          (_, i) =>
            `Paragraph ${i}: a detailed outcome and its verification.\n`,
        ).join("\n"),
      },
    },
  );
  const changed = await request.put(
    `/api/workspaces/${await projectWorkspace(page.request, "tooltip-overflow")}/tasks/T-1`,
    {
      data: {
        expected_revision: 1,
        body: Array.from(
          { length: 80 },
          (_, i) =>
            `Updated paragraph ${i}: a detailed outcome and its verification.\n`,
        ).join("\n"),
      },
    },
  );
  expect(changed.ok()).toBe(true);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "tooltip-overflow")}/tasks/T-1/history`,
  );
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail).toBeVisible();

  await expect(detail.locator(".activity-entry > summary")).toHaveCount(2);
  for (const row of await detail.locator(".activity-entry > summary").all())
    await row.click();
  await expect(detail.locator(".task-changes")).toHaveCount(2);
  await expect(detail.getByText("Loading changes…")).toHaveCount(0);
  const bounds = () =>
    page.evaluate(() =>
      [
        document.documentElement,
        ...document.querySelectorAll(
          ".workspace-pane, [data-panel], [data-panel] > div, .workspace-project-view",
        ),
      ].map((node) => ({
        height: node.clientHeight,
        scroll: node.scrollHeight,
      })),
    );
  const original = await bounds();
  expect(original.every(({ height, scroll }) => scroll === height)).toBe(true);
  await expect(page.locator("[data-panel] > div").first()).toHaveCSS(
    "overflow",
    "hidden",
  );
  for (const time of await detail.locator("time:visible").all()) {
    await time.scrollIntoViewIfNeeded();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await time.hover();
    await expect(page.getByRole("tooltip")).toBeVisible();
    expect(await bounds()).toEqual(original);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    expect(await bounds()).toEqual(original);
  }
});
