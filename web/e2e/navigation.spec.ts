import { openResource } from "./shell-navigation";
import {
  fixtureCliProject,
  projectWorkspace,
  fixtureProject,
  fixtureMcpProject,
  choose,
  fixtureStages,
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
    "--prefix",
    "NVO",
  ]);
  const call = await connectMcp(request);
  const workspace = await projectWorkspace(request, "navigation");
  const otherWorkspace = await projectWorkspace(request, "nav-other");
  const one = await call("create_task", {
    project_id: "navigation",
    task: {
      stages: fixtureStages(),
      title: "Prepare the export",
      status: "up_next",
    },
  });
  const two = await call("create_task", {
    project_id: "navigation",
    task: {
      stages: fixtureStages(),
      title: "Download the export",
      status: "up_next",
      dependencies: [one.key],
    },
  });
  const old = await call("create_task", {
    project_id: "navigation",
    task: { stages: fixtureStages(), title: "Earlier experiment" },
  });
  await call("edit_task", {
    project_id: "navigation",
    task_id: old.key,
    changes: { expected_revision: 1, archived: true },
  });
  expect([one.key, two.key, old.key]).toEqual(["NAV-1", "NAV-2", "NAV-3"]);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`/projects/navigation/tasks/${two.key}`);
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
  await page.goto(`/workspaces/${workspace}`);
  const blocker = page.getByRole("link", { name: "NAV-1", exact: true });
  await blocker.hover();
  await expect(page.getByRole("tooltip")).toHaveText("Prepare the export");
  await page.keyboard.press("Escape");
  await expect(blocker).toHaveAttribute(
    "href",
    `/projects/navigation/tasks/NAV-1`,
  );
  await expect(
    page.getByText("Completion criteria set", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Add completion criteria", { exact: true }),
  ).toHaveCount(0);
  await blocker.click();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: "Prepare the export", exact: true }),
  ).toContainText("Prepare the export");
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: "Prepare the export", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Unsaved title");
  await page.goBack();
  await expect(page).toHaveURL(`/workspaces/${workspace}`);
  await page.goForward();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Unsaved title");
  await page.goBack();
  await expect(page).toHaveURL(`/workspaces/${workspace}`);
  await expect(editor).toHaveCount(0);
  await page.goForward();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: "Prepare the export", exact: true }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", {
      name: /NAV-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  const prerequisite = editor.getByRole("link", { name: "NAV-1", exact: true });
  await expect(editor.getByLabel("Current task definition")).toBeVisible();
  await prerequisite.click();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  await page.goBack();
  await expect(page).toHaveURL(/tasks\/NAV-2(?:\/dependencies)?$/);
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: "Download the export", exact: true }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .getByLabel("Workspace", { exact: true })
    .selectOption(otherWorkspace);
  await expect(page).toHaveURL(`/workspaces/${otherWorkspace}`);
  await page.goBack();
  await expect(page).toHaveURL(`/workspaces/${workspace}`);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", {
      name: /NAV-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: "Download the export", exact: true }),
  ).toBeVisible();
  const renamed = await call("edit_task", {
    project_id: "navigation",
    task_id: one.key,
    changes: {
      expected_revision: 1,
      title: "Prepare the corrected export",
      labels: ["bug"],
    },
  });
  expect(renamed.key).toBe("NAV-1");
  await prerequisite.hover();
  await expect(page.getByRole("tooltip")).toHaveText(
    "Prepare the corrected export",
  );
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto("/projects/navigation/tasks/NAV-3");
  await expect(page).toHaveURL(/tasks\/NAV-3$/);
  await expect(
    page.getByRole("region", { name: "Archived tasks", includeHidden: true }),
  ).toBeHidden();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: "Earlier experiment", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    editor.getByRole("button", { name: "Restore", exact: true }),
  ).toHaveCount(0);
  await page.goto("/projects/navigation/tasks/NAV-999");
  await expect(
    page.getByRole("heading", { name: "Task not found", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "New task", exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("collections open and close entity details through the same routes", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "collection-layout";
  const { workspace_id } = await fixtureMcpProject(call, {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "COL",
  });
  await call("create_milestone", {
    workspace_id,
    milestone: { id: "group", title: "Delivery" },
  });
  await call("create_task", {
    project_id,
    task: { stages: fixtureStages(), id: "retired", title: "Retired work" },
  });
  await call("edit_task", {
    project_id,
    task_id: "retired",
    changes: { expected_revision: 1, archived: true },
  });

  for (const path of ["milestones", "archive"]) {
    await page.goto(`/projects/${project_id}/${path}`);
    const row =
      path === "milestones"
        ? page
            .locator(".task-card")
            .getByRole("link", { name: "Delivery", exact: true })
            .first()
        : page.locator(".archive-list .task-card-link").first();
    await expect(row).toBeVisible();

    await row.click();
    await expect(page.locator(".entity-overlay, .entity-pane")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(
      page.locator("[data-slot=dialog-content][data-state=open]"),
    ).toHaveCount(0);
  }
});

test("task metadata and internal Markdown links stay inside the router", async ({
  page,
  request,
}) => {
  const project_id = "link-routing";
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "LNK",
    },
  });
  const workspace = await projectWorkspace(request, project_id);
  const milestone = await request.post(
    `/api/workspaces/${workspace}/milestones`,
    { data: { id: "questions", title: "Export milestone" } },
  );
  expect(milestone.ok()).toBe(true);
  await request.post(`/api/projects/${project_id}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "export",
      title: "Export books",
      milestone_id: "questions",
      body: `[Workspace milestones](/workspaces/${workspace}/milestones)`,
    },
  });
  await page.goto(`/projects/${project_id}/tasks/LNK-1`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await editor
    .getByRole("link", { name: "Workspace milestones", exact: true })
    .click();
  await expect(page).toHaveURL(`/workspaces/${workspace}/milestones`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  await page.goBack();
  await editor.getByRole("link", { name: "M-1", exact: true }).click();
  await expect(page).toHaveURL(`/workspaces/${workspace}/milestones/M-1`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  await page.keyboard.press("Escape");
  await openResource(page, "Milestones");
  await page.locator(".task-card-link").first().click();
  await page.getByRole("link", { name: "View tasks", exact: true }).click();
  await expect(page).toHaveURL(`/workspaces/${workspace}?milestone=questions`);
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("questions");
  await expect(
    page.getByRole("link", { name: /LNK-1 (?:Feature )?Export books/ }),
  ).toBeVisible();
});

test("shared panes preserve the workspace and related return paths on mobile", async ({
  page,
  request,
}) => {
  const id = "edit"; // A project ID must not be mistaken for an editor route.
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)), task_prefix: "UNI" },
  });
  const workspace = await projectWorkspace(request, id);
  await request.post(`/api/workspaces/${workspace}/milestones`, {
    data: { id: "group", title: "First delivery" },
  });
  await request.post(`/api/projects/${id}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "one",
      title: "A focused task",
      milestone_id: "group",
    },
  });
  await page.goto(`/projects/${id}`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const board = page.locator(".board");
  await expect(board).toBeVisible();

  await expect(
    page.getByRole("tablist", { name: "Open resources", exact: true }),
  ).toContainText("Inbox");
  await expect(
    page
      .locator(".workspace-frame .queue-controls")
      .getByRole("button", { name: "Run queue" }),
  ).toBeVisible();
  await expect(page.locator(".workspace-frame .queue-controls")).toContainText(
    "0 of 1 workers active",
  );
  await expect(page.getByText("Local workspace · Preview")).toHaveCount(0);
  await page.getByLabel("Filter by milestone").selectOption("group");
  const card = page.locator(".task-card-link");
  await card.click();
  const dialog = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(dialog).toBeVisible();

  await expect(dialog.locator(".task-definition")).toBeVisible();
  await page.keyboard.press("Tab");
  expect(
    await dialog.evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page.keyboard.press("Escape");
  if (await dialog.count()) await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(card).toBeFocused();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("group");
  await openResource(page, "Milestones");
  const row = page.locator(".task-card");
  await expect(row).toBeVisible();
  await expect(
    page.getByRole("tablist", { name: "Open resources" }),
  ).toBeVisible();
  await page.locator(".task-card-link").first().click();
  await page.getByRole("link", { name: "View tasks", exact: true }).click();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("group");
  await card.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /^(Close editor|Back)$/ }).click();
  await expect(page).toHaveURL(`/workspaces/${workspace}?milestone=group`);
  await openResource(page, "Workspace settings");
  await expect(
    page.getByRole("region", { name: "Workspace settings", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Workspace settings", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(`/workspaces/${workspace}`);
  await openResource(page, "Milestones");
  await expect(page.locator(".milestone-board")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "New milestone", exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await openResource(page, "Board");
  await expect(
    page.getByRole("button", { name: "New task", exact: true }),
  ).toHaveCount(0);
  // The earlier explicit reload is the only document navigation in this journey.
  expect(await page.evaluate(() => performance.timeOrigin)).toBeGreaterThan(
    boot,
  );
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
          task_prefix: "HRT",
        },
      })
    ).ok(),
  ).toBeTruthy();
  await request.post(`/api/projects/${id}/tasks`, {
    data: { stages: fixtureStages(), id: "one", title: "History task" },
  });
  const call = await connectMcp(request);
  await call("ask_question", {
    project_id: id,
    question: {
      id: "scope",
      task_id: "HRT-1",
      question: "Which scope?",
      context: "Choose the scope",
      recommendation: "Small",
    },
  });
  const workspace = await projectWorkspace(request, id);
  const board = `/workspaces/${workspace}`;
  const inbox = `${board}/inbox`;
  await page.goto(board);
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await page
    .getByRole("link", { name: "HRT-1: Which scope?", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back)$/,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(/conversation\/question:scope:1$/);
  await page.goBack();
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(board);
  await page.goForward();
  await expect(page).toHaveURL(inbox);
  await page.goForward();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  const answer = page.getByRole("textbox", {
    name: "Your answer",
    exact: true,
  });
  await expect(answer).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(answer).not.toBeFocused();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(inbox);
  await openResource(page, "Board");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /HRT-1/ })
    .click();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back)$/,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(board);
  // Direct links retain the collection fallback; explicit new tabs start fresh.
  await page.goto(`/projects/${id}/tasks/HRT-1`);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL(board);
});

test("entity identity and drafts persist across task and workspace tabs", async ({
  page,
  request,
}) => {
  const project = "identity-layout";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, project)), task_prefix: "IDN" },
  });
  const workspace = await projectWorkspace(request, project);
  await request.post(`/api/workspaces/${workspace}/milestones`, {
    data: { id: "first", title: "First milestone" },
  });
  await request.post(`/api/projects/${project}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "first",
      title: "First task",
      milestone_id: "first",
    },
  });
  await page.goto("/");
  const sidebar = page.locator('[data-slot="sidebar"]');
  const logo = sidebar.getByRole("link", { name: "Flowfield", exact: true });
  const projectLink = sidebar.getByLabel("Workspace", { exact: true });
  await expect(projectLink).toBeVisible();
  await expect(
    sidebar.getByRole("button", { name: "Board", exact: true }),
  ).toHaveCount(0);

  await expect(logo).toHaveAttribute("href", "/");

  await projectLink.selectOption(workspace);
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /IDN-1/ })
    .click();
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const title = editor.getByRole("heading", {
    name: "First task",
    exact: true,
  });

  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await editor
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .click();
  await expect(title).toBeVisible();
  await expect(
    editor.getByRole("tab", { name: "Activity", exact: true }),
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
  await openResource(page, "Workspace settings");
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
    fixtureCliProject([
      existingDirectory(join(state, project)),
      "--prefix",
      "TIM",
    ]);
    const task = cli([
      "task",
      "create",
      "--title",
      "Timestamp evidence",
      "--project",
      project,
      "--body",
      "Keep timestamp evidence accessible.",
    ]);
    const created = Date.parse(task.updated_at);
    await page.clock.install({ time: new Date(created) });
    await page.clock.pauseAt(new Date(created + 10000));
    await page.goto(`/projects/${project}/tasks/TIM-1/history`);
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
    await expect(page.locator('[data-message-id="plan:1"]')).toBeVisible();
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
      task_prefix: "SBN",
    },
  });
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, "short-name")), name: "Short" },
  });
  const workspace = await projectWorkspace(request, "sidebar-names");
  const shortWorkspace = await projectWorkspace(request, "short-name");
  const path = "/api/projects/sidebar-names";
  await request.post(path + "/tasks", {
    data: {
      stages: fixtureStages(),
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
  await page.goto(`/workspaces/${workspace}`);
  const settings = page.getByRole("dialog", {
    name: "Coordinator model settings",
    exact: true,
  });
  await settings.getByRole("button", { name: "Cancel", exact: true }).click();
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
      task_prefix: "WSF",
    },
  });
  await request.post("/api/projects/workspace-frame/tasks", {
    data: { stages: fixtureStages(), title: "Review the workspace layout" },
  });
  const workspace = await projectWorkspace(request, "workspace-frame");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/projects/workspace-frame");
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
  const divider = page.getByRole("separator", {
    name: "Resize coordinator and work",
  });
  const pane = page.locator(".workspace-pane").first();
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
  const heading = detail.locator(".entity-overlay-title h2");
  expect((await back.boundingBox())!.x).toBeLessThan(
    (await heading.boundingBox())!.x,
  );
  await page.screenshot({
    path: testInfo.outputPath("task-navigation-desktop.png"),
  });
  await openResource(page, "Board");
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
    await openResource(page, "Board");
    await expect(detail).not.toBeVisible();
    await page.goBack();
    await expect(detail).toBeVisible();
    await back.click();
    await expect(card).toBeFocused();
  }
  await expect(
    page.getByRole("button", { name: /Collapse projects|Expand projects/ }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("tab", { name: "Appearance", exact: true }).click();
  await choose(page.getByLabel("Theme", { exact: true }), "light");
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
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
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("tab", { name: "Appearance", exact: true }).click();
  await choose(page.getByLabel("Theme", { exact: true }), "system");
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
  await expect(page.locator("html")).toHaveClass("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.getByRole("tab", { name: /^Board/ }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: /^Inbox/ })).toBeFocused();
  await expect(page).toHaveURL(`/workspaces/${workspace}`);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inbox$/);
  await page.goBack();
  await openResource(page, "Milestones");
  await expect(page).toHaveURL(/\/milestones$/);
  await page.goBack();
  await expect(page.getByRole("tab", { name: /^Board/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Open workspaces", exact: true })
    .click();
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
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
  await expect(page.getByRole("tab", { name: /^Board/ })).toHaveAttribute(
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
      task_prefix: "TOV",
    },
  });
  await request.post("/api/projects/tooltip-overflow/tasks", {
    data: {
      stages: fixtureStages(),
      title: "Long definition",
      body: Array.from(
        { length: 70 },
        (_, i) => `Paragraph ${i}: a detailed outcome and its verification.\n`,
      ).join("\n"),
    },
  });
  const changed = await request.put(
    "/api/projects/tooltip-overflow/tasks/TOV-1",
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
  await page.goto("/projects/tooltip-overflow/tasks/TOV-1/history");
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail).toBeVisible();

  await expect(detail.locator(".activity-entry > summary")).toHaveCount(3);
  for (const row of await detail.locator(".activity-entry > summary").all())
    await row.click();
  await expect(detail.locator(".task-changes summary")).toHaveCount(2);
  for (const summary of await detail.locator(".task-changes summary").all()) {
    await summary.click();
  }
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
  for (const time of await detail.locator("time").all()) {
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
