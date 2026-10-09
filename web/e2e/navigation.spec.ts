import { choose, fixtureStages } from "./support";
import { join } from "node:path";

import { expect } from "@playwright/test";
import {
  test,
  existingDirectory,
  state,
  cli,
  closeOverlay,
  connectMcp,
} from "./support";

test("task keys link to persistent selections with history and draft protection", async ({
  page,
  request,
}) => {
  cli([
    "project",
    "init",
    existingDirectory(join(state, "navigation")),
    "--name",
    "Navigation trial",
  ]);
  cli([
    "project",
    "init",
    existingDirectory(join(state, "nav-other")),
    "--name",
    "Other navigation",
    "--prefix",
    "NVO",
  ]);
  const call = await connectMcp(request);
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
  await page.goto("/projects/navigation");
  const blocker = page
    .locator(".work-state")
    .getByRole("link", { name: "NAV-1", exact: true });
  await blocker.hover();
  await expect(page.getByRole("tooltip")).toHaveText("Prepare the export");
  await page.keyboard.press("Escape");
  await expect(blocker).toHaveAttribute(
    "href",
    "/projects/navigation/tasks/NAV-1",
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
      .getByRole("heading", { name: /NAV-1/ }),
  ).toContainText("NAV-1");
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: / Prepare the export$/ }),
  ).toBeVisible();
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Unsaved title");
  await page.goBack();
  await expect(page).toHaveURL(/projects\/navigation$/);
  await page.goForward();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Unsaved title");
  await page.goBack();
  await expect(page).toHaveURL(/projects\/navigation$/);
  await expect(editor).toHaveCount(0);
  await page.goForward();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: / Prepare the export$/ }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .getByRole("link", {
      name: /NAV-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  const prerequisite = editor
    .locator(".work-state")
    .getByRole("link", { name: "NAV-1", exact: true });
  await expect(editor.getByText("Task defined", { exact: true })).toBeVisible();
  await prerequisite.click();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  await page.goBack();
  await expect(page).toHaveURL(/tasks\/NAV-2(?:\/dependencies)?$/);
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: / Download the export$/ }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .getByRole("link", { name: "Other navigation", exact: true })
    .click();
  await expect(page).toHaveURL(/projects\/nav-other$/);
  await page.goBack();
  await expect(page).toHaveURL(/projects\/navigation$/);
  await page
    .getByRole("link", {
      name: /NAV-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("heading", { name: / Download the export$/ }),
  ).toBeVisible();
  const renamed = await call("edit_task", {
    project_id: "navigation",
    task_id: one.key,
    changes: {
      expected_revision: 1,
      title: "Prepare the corrected export",
      task_type: "bug",
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
      .getByRole("heading", { name: / Earlier experiment$/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    editor.getByRole("button", { name: "Restore", exact: true }),
  ).toBeVisible();
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
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "COL",
  });
  await call("create_milestone", {
    project_id,
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
    const row = page.locator(".collection-row").first();
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
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "LNK",
    },
  });
  const milestone = await request.post(
    `/api/projects/${project_id}/milestones`,
    { data: { id: "questions", title: "Export milestone" } },
  );
  expect(milestone.ok()).toBe(true);
  await request.post(`/api/projects/${project_id}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "export",
      title: "Export books",
      milestone_id: "questions",
      body: `[Project milestones](/projects/${project_id}/milestones)`,
    },
  });
  await page.goto(`/projects/${project_id}/tasks/LNK-1`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await editor
    .getByRole("link", { name: "Project milestones", exact: true })
    .click();
  await expect(page).toHaveURL(`/projects/${project_id}/milestones`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  await page.goBack();
  await editor.getByRole("link", { name: "M-1", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project_id}/milestones/M-1`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  await page.getByRole("button", { name: "View tasks", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project_id}`);
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("questions");
  await expect(
    page.getByRole("link", { name: /LNK-1 (?:Feature )?Export books/ }),
  ).toBeVisible();
});

test("shared overlays preserve the workspace, related return paths and mobile creation", async ({
  page,
  request,
}) => {
  const id = "edit"; // A project ID must not be mistaken for an editor route.
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, id)), task_prefix: "UNI" },
  });
  await request.post(`/api/projects/${id}/milestones`, {
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
    page.getByRole("tablist", { name: "Project views", exact: true }),
  ).toContainText("Needs you");
  await expect(page.locator(".up_next .queue-controls")).toContainText(
    "Run queue",
  );
  await expect(page.locator(".up_next .queue-controls")).toContainText(
    "0/1 active",
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
  await page.getByRole("tab", { name: /^Milestones(?: \d+)?$/ }).click();
  const row = page.locator(".milestone-list .collection-row");
  await row.click();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await page.getByRole("button", { name: "View tasks", exact: true }).click();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("group");
  await card.click();
  await expect(dialog).toBeVisible();
  await dialog
    .getByRole("button", { name: /^(Close editor|Back to board)$/ })
    .click();
  await expect(page).toHaveURL(`/projects/${id}`);
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Project details", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("dialog", { name: "Project details", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(`/projects/${id}`);
  await page.getByRole("tab", { name: /^Milestones(?: \d+)?$/ }).click();
  await expect(page.locator(".milestone-list")).toBeVisible();
  await page
    .getByRole("button", { name: "New milestone", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "New milestone", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Title", { exact: true }).fill("Draft milestone");
  // Releasing/repeating Escape cannot dismiss the confirmation opened by that key.
  await page.keyboard.down("Escape");
  const discard = page.getByRole("alertdialog", {
    name: "Discard your unsaved edits?",
  });
  await expect(discard).toBeVisible();
  await page.keyboard.up("Escape");
  await page.keyboard.press("Escape");
  await expect(discard).toBeVisible();
  await expect(
    discard.getByRole("button", { name: "Keep editing" }),
  ).toBeFocused();
  await discard.getByRole("button", { name: "Keep editing" }).click();
  await expect(page.getByLabel("Title", { exact: true })).toBeFocused();
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue(
    "Draft milestone",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Add project", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /^Board / }).click();
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const create = page.getByRole("dialog", { name: "New task", exact: true });

  await page.getByLabel("Title", { exact: true }).fill("A mobile draft");

  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(create).toHaveCount(0);
  expect(await page.evaluate(() => document.body.style.overflow)).not.toBe(
    "hidden",
  );
  // The earlier explicit reload is the only document navigation in this journey.
  expect(await page.evaluate(() => performance.timeOrigin)).toBeGreaterThan(
    boot,
  );
});

test("closing entity overlays returns through history without duplicate collections", async ({
  page,
  request,
}) => {
  const id = "history-roundtrip";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, id)), task_prefix: "HIS" },
  });
  await request.post(`/api/projects/${id}/tasks`, {
    data: { stages: fixtureStages(), id: "one", title: "History task" },
  });
  const call = await connectMcp(request);
  await call("ask_question", {
    project_id: id,
    question: {
      id: "scope",
      task_id: "HIS-1",
      question: "Which scope?",
      context: "Choose the scope",
      recommendation: "Small",
    },
  });
  const board = `/projects/${id}`;
  const inbox = `${board}/inbox`;
  await page.goto(board);
  await page.getByRole("tab", { name: /^Needs you(?: \d+)?$/ }).click();
  await page.locator(".attention-card").first().click();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
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
  const answer = page.getByLabel("Your answer", { exact: true });
  await expect(answer).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(answer).not.toBeFocused();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(board);
  await page.getByRole("link", { name: /HIS-1/ }).first().click();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(board);
  // Direct links have no known in-app origin, so Close stays in the project.
  await page.goto(`${board}/tasks/HIS-1`);
  await page
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(board);
});

test("entity identity and drafts persist across task and project tabs", async ({
  page,
  request,
}) => {
  const project = "identity-layout";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "IDN" },
  });
  await request.post(`/api/projects/${project}/milestones`, {
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
  const projectLink = sidebar.getByRole("link", { name: project, exact: true });
  await expect(projectLink).toBeVisible();
  await expect(
    sidebar.getByRole("button", { name: "Board", exact: true }),
  ).toHaveCount(0);

  await expect(logo).toHaveAttribute("href", "/");

  await projectLink.click();
  await page.getByRole("link", { name: /IDN-1/ }).click();
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const title = editor.getByRole("heading", {
    name: "IDN-1 · First task",
    exact: true,
  });

  await editor
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .click();
  await expect(title).toBeVisible();
  await expect(editor.getByRole("tab")).toHaveCount(0);
  await expect(
    editor.getByText("Loading execution…", { exact: true }),
  ).toHaveCount(0);
  await expect(editor.getByLabel("Add prerequisite")).toHaveCount(0);
  await expect(
    editor.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "New task", exact: true }).click();
  const creation = page.getByRole("region", { name: "New task", exact: true });
  await expect(creation.getByRole("tab")).toHaveCount(0);
  await expect(creation.getByLabel("Title", { exact: true })).toBeVisible();
  await expect(creation.getByLabel("Add prerequisite")).toHaveCount(0);
  await page.keyboard.press("Escape");
  let releaseSettings!: () => void;
  const settingsReady = new Promise<void>((resolve) => {
    releaseSettings = resolve;
  });
  await page.route(`**/api/projects/${project}/workers`, async (route) => {
    await settingsReady;
    await route.continue();
  });
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(page).toHaveURL(
    new RegExp(`/projects/${project}/edit/workers$`),
  );
  await expect(page.getByLabel("Maximum parallel workers")).toBeDisabled();
  releaseSettings();
  await page.getByLabel("Maximum parallel workers").fill("3");
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await page
    .getByLabel("Destination branch", { exact: true })
    .fill("draft-target");
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(page.getByLabel("Maximum parallel workers")).toHaveValue("3");
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(
    page.getByLabel("Destination branch", { exact: true }),
  ).toHaveValue("draft-target");
  // Switch to mobile, then deliberately discard the drafts on refresh.
  await page.setViewportSize({ width: 390, height: 844 });

  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "Integration", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByLabel("Destination branch", { exact: true }),
  ).toHaveValue("");
});

test.describe("relative timestamps", () => {
  test.use({ locale: "en-US", timezoneId: "America/Denver" });

  test("timestamps refresh and expose the exact local time on hover and focus", async ({
    page,
  }) => {
    const project = "relative-times";
    cli([
      "project",
      "init",
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
    await page.goto(`/projects/${project}/tasks/TIM-1`);
    const card = page.locator(".conversation-message").first();
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

test("sidebar rail names remain accessible and idle input opens deliberately", async ({
  page,
  request,
}) => {
  const name = "A carefully tended collection of pocket gardens";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "sidebar-names")),
      name,
      task_prefix: "SBN",
    },
  });
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, "short-name")), name: "Short" },
  });
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
  // Other journeys adopt projects concurrently. Keep this rail's actual records
  // scoped so a new sibling project cannot scroll the focused trigger away.
  await page.route("**/api/projects", async (route) => {
    const response = await route.fetch();
    const projects = await response.json();
    await route.fulfill({
      json: projects.filter((project: { id: string }) =>
        ["sidebar-names", "short-name"].includes(project.id),
      ),
    });
  });
  await page.goto("/projects/sidebar-names");
  const settings = page.getByRole("dialog", {
    name: "Coordinator model settings",
    exact: true,
  });
  await settings.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(settings).not.toBeVisible();
  const sidebar = page.getByRole("navigation", {
    name: "Projects",
    exact: true,
  });
  const short = sidebar.getByRole("link", { name: "Short", exact: true });
  await short.scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await short.hover();
  await expect(page.getByRole("tooltip")).toHaveText("Short");
  await page.keyboard.press("Escape");
  const long = sidebar.getByRole("link", { name, exact: true });
  await long.scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await long.hover();
  await expect(page.getByRole("tooltip")).toHaveText(name);
  await page.keyboard.press("Escape");
  await page.mouse.move(0, 0);
  await short.focus();
  await expect(page.getByRole("tooltip")).toHaveText("Short");
  await page.keyboard.press("Tab");
  await expect(long).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText(name);
  await page.keyboard.press("Escape");
  // Mobile uses a conventional project drawer; project views remain above work.
  await page.setViewportSize({ width: 650, height: 844 });
  await page
    .getByRole("button", { name: "Open projects", exact: true })
    .click();
  await expect(long).toBeVisible();
  await expect(long.getByLabel("Needs you: 1")).toHaveText("1");
  await long.click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: /^Needs you 1$/ })).toBeVisible();
  await expect(
    page.getByRole("link", { name: /item needs your attention/ }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: /Read the current agreement/ }).click();
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
    page.getByRole("group", { name: "Message task context" }),
  ).toContainText("Read the current agreement");
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
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "workspace-frame")),
      name: "Workspace frame",
      task_prefix: "WSF",
    },
  });
  await request.post("/api/projects/workspace-frame/tasks", {
    data: { stages: fixtureStages(), title: "Review the workspace layout" },
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/projects/workspace-frame");
  await expect(page.locator("html")).toHaveClass("dark");
  await expect(
    page.getByRole("heading", { name: "Workspace frame", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("separator", { name: "Resize coordinator and work" }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveCount(0);
  const headers = page.locator(".workspace-pane-header");
  const headerBounds = await headers.evaluateAll((nodes) =>
    nodes.map((node) => ({
      top: node.getBoundingClientRect().top,
      height: node.getBoundingClientRect().height,
    })),
  );
  expect(headerBounds[0]).toEqual(headerBounds[1]);
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
  const projectTabs = page.locator(".workspace-project-tabs");
  expect(
    await projectTabs.evaluate(
      (node) => node.scrollHeight === node.clientHeight,
    ),
  ).toBe(true);
  const card = page.getByRole("link", { name: /Review the workspace layout/ });
  await card.click();
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail).toBeVisible();
  await expect(
    page.getByRole("tablist", { name: "Project views" }),
  ).toBeVisible();
  const back = detail.getByRole("button", { name: "Back to board" });
  const heading = detail.getByRole("heading", { name: /WSF-1/ });
  expect((await back.boundingBox())!.x).toBeLessThan(
    (await heading.boundingBox())!.x,
  );
  await page.screenshot({
    path: testInfo.outputPath("task-navigation-desktop.png"),
  });
  await page.getByRole("tab", { name: /^Board/ }).click();
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
    await page.getByRole("tab", { name: /^Board/ }).click();
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
  await page
    .getByRole("link", { name: "Workspace frame", exact: true })
    .click();
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
  await page
    .getByRole("link", { name: "Workspace frame", exact: true })
    .click();
  await expect(page.locator("html")).toHaveClass("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.getByRole("tab", { name: /^Board/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Needs you/ })).toBeFocused();
  await expect(page).toHaveURL(/\/projects\/workspace-frame$/);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inbox$/);
  await page.goBack();
  await page.getByRole("tab", { name: /^Milestones/ }).click();
  await expect(page).toHaveURL(/\/milestones$/);
  await page.goBack();
  await expect(page.getByRole("tab", { name: /^Board/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Open projects", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Workspace frame", exact: true })
    .click();
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
  await page.getByRole("link", { name: /Review the workspace layout/ }).click();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tablist", { name: "Project views" }),
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
  await request.post("/api/projects/initialize", {
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
  await page.goto("/projects/tooltip-overflow/tasks/TOV-1");
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail).toBeVisible();
  await detail
    .getByRole("button", { name: /Expand/ })
    .first()
    .click();
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
