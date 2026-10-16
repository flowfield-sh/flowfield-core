import { openResource } from "./shell-navigation";
import {
  fixtureCliProject,
  projectWorkspace,
  fixtureProject,
  fixtureMcpProject,
  fixtureProgress,
  fixtureStages,
  test,
  existingDirectory,
  state,
  checkout,
  cli,
  closeOverlay,
  ensureEditing,
  connectMcp,
} from "./support";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";

test("board failures use toasts and keep recovery beside retained work", async ({
  page,
  request,
}) => {
  expect(
    (
      await fixtureProject(request, {
        data: {
          path: existingDirectory(join(state, "board-recovery")),
          task_prefix: "BRC",
        },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post("/api/projects/board-recovery/tasks", {
        data: { stages: fixtureStages(), title: "Preserved board work" },
      })
    ).ok(),
  ).toBe(true);
  let fail = true;
  const workspace = await projectWorkspace(request, "board-recovery");
  await page.route(`**/api/workspaces/${workspace}/view/board`, (route) =>
    fail
      ? route.fulfill({
          status: 503,
          json: {
            error: { code: "unavailable", message: "Temporary board failure" },
          },
        })
      : route.continue(),
  );
  await page.goto("/projects/board-recovery");
  const alert = page
    .locator("[data-sonner-toast][data-type=error]")
    .filter({ hasText: "Temporary board failure" });
  await expect(
    page.getByRole("heading", { name: "Could not open workspace" }),
  ).toBeVisible();
  await expect(alert).toContainText("Temporary board failure");
  await expect(page.getByRole("button", { name: "Retry board" })).toBeVisible();
  fail = false;
  const card = page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Preserved board work/ });
  // A live project event can restore the board before the retry click lands.
  await expect(async () => {
    if (!(await card.isVisible()))
      await page
        .getByRole("button", { name: "Retry board" })
        .click({ timeout: 1000 });
    await expect(card).toBeVisible();
  }).toPass({ timeout: 5000 });
  await expect(page.getByRole("button", { name: "Retry board" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("status", { name: "Connected", exact: true }),
  ).toBeVisible();
  fail = true;
  expect(
    (
      await request.post("/api/projects/board-recovery/tasks", {
        data: { stages: fixtureStages(), title: "New work after recovery" },
      })
    ).ok(),
  ).toBe(true);
  await expect(alert).toContainText("Temporary board failure");
  await expect(
    page.getByRole("button", { name: "Refresh board" }),
  ).toBeVisible();
  await expect(card).toBeVisible();
  fail = false;
  const recovered = page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /New work after recovery/ });
  // Another live project event can recover the read before the retry click.
  // Both paths must restore the new work, without losing the existing card.
  await expect(async () => {
    if (!(await recovered.isVisible()))
      await page
        .getByRole("button", { name: "Refresh board" })
        .click({ timeout: 1000 });
    await expect(recovered).toBeVisible();
  }).toPass({ timeout: 5000 });
  await expect(page.getByRole("button", { name: "Refresh board" })).toHaveCount(
    0,
  );
});

test("three-task board across CLI, browser and MCP, with archive and mobile reading", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const project = join(state, "harbor");
  fixtureCliProject([existingDirectory(project), "--name", "Harbor"]);
  const workspace = await projectWorkspace(request, "harbor");
  cli([
    "project",
    "edit",
    "--project",
    "harbor",
    "--description",
    "Make expense reporting effortless",
  ]);
  cli([
    "milestone",
    "create",
    "--workspace",
    workspace,
    "--id",
    "csv",
    "--title",
    "CSV export",
  ]);
  for (const [id, title, type] of [
    ["serializer", "Build CSV serializer", "feature"],
    ["button", "Add download button", "feature"],
    ["rounding", "Fix total rounding", "bug"],
  ]) {
    cli([
      "task",
      "create",
      "--project",
      "harbor",
      "--id",
      id,
      "--title",
      title,
      "--label",
      type,
      "--status",
      "up-next",
      "--body",
      "Verification passes",
      ...(id === "rounding" ? [] : ["--milestone", "csv"]),
    ]);
  }
  await page.goto("/projects/harbor");
  await expect(page.getByLabel("Workspace", { exact: true })).toBeVisible();
  await expect(page.getByText("Ship CSV export", { exact: true })).toHaveCount(
    0,
  );
  const call = await connectMcp(request);
  const rounding = page
    .locator(".card-shell")
    .filter({ hasText: "Fix total rounding" });
  const backlog = page.getByRole("region", {
    name: "Backlog",
    exact: true,
    includeHidden: true,
  });
  const upNext = page.getByRole("region", {
    name: "Up next",
    exact: true,
    includeHidden: true,
  });
  await expect(rounding).not.toHaveAttribute("draggable", "true");
  await call("prioritize_task", {
    project_id: "harbor",
    task_id: "rounding",
    priority: { expected_revision: 1, status: "backlog" },
  });
  await expect(backlog).toContainText("Fix total rounding");
  const queued = await call("get_task", {
    project_id: "harbor",
    task_id: "rounding",
  });
  await call("prioritize_task", {
    project_id: "harbor",
    task_id: "rounding",
    priority: { expected_revision: queued.revision, status: "up_next" },
  });
  await expect(upNext).toContainText("Fix total rounding");
  const current = await call("get_task", {
    project_id: "harbor",
    task_id: "rounding",
  });
  await fixtureProgress(request, {
    project_id: "harbor",
    task_id: "rounding",
    progress: { expected_revision: current.revision, status: "in_progress" },
  });
  await expect(
    page.getByRole("region", {
      name: "In progress",
      exact: true,
      includeHidden: true,
    }),
  ).toContainText("Fix total rounding");
  await expect(rounding).not.toHaveAttribute("draggable", "true");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Fix total rounding/ })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("button", { name: "Archive", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Move task", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /^(Close editor|Back)$/ }).click();
  const button = await call("get_task", {
    project_id: "harbor",
    task_id: "button",
  });
  await call("prioritize_task", {
    project_id: "harbor",
    task_id: "button",
    priority: {
      expected_revision: button.revision,
      status: "up_next",
      before_id: "serializer",
    },
  });
  await expect(
    page
      .getByRole("region", {
        name: "Up next",
        exact: true,
        includeHidden: true,
      })
      .locator(".task-card")
      .first(),
  ).toContainText("Add download button");
  await page.getByRole("link", { name: /Add download button/ }).click();
  const toArchive = await call("get_task", {
    project_id: "harbor",
    task_id: "button",
  });
  await call("edit_task", {
    project_id: "harbor",
    task_id: "button",
    changes: { expected_revision: toArchive.revision, archived: true },
  });
  await expect(
    page
      .getByRole("region", {
        name: "Up next",
        exact: true,
        includeHidden: true,
      })
      .locator(".task-card"),
  ).toHaveCount(0);
  expect(cli(["task", "show", "button", "--project", "harbor"]).status).toBe(
    "up_next",
  );
  await closeOverlay(page);
  await expect(page.locator(".up_next .task-card")).toHaveCount(1);
  await expect(page.getByLabel("Filter by project")).toHaveValue("harbor");
  await openResource(page, "Archive");
  await expect(
    page.getByRole("link", { name: /Add download button/ }),
  ).toBeVisible();
  await expect(page.locator(".column")).toHaveCount(0);
  await page.getByRole("link", { name: /Add download button/ }).click();
  const archived = await call("get_task", {
    project_id: "harbor",
    task_id: "button",
  });
  await call("edit_task", {
    project_id: "harbor",
    task_id: "button",
    changes: { expected_revision: archived.revision, archived: false },
  });
  await closeOverlay(page);
  await openResource(page, "Tasks");
  await page.getByLabel("Filter by milestone").selectOption("csv");
  await expect(page.locator(".task-card")).toHaveCount(2);
  await openResource(page, "Milestones");
  await page.locator(".milestone-board .task-card-link").first().click();
  await call("edit_milestone", {
    workspace_id: workspace,
    milestone_id: "csv",
    changes: {
      expected_revision: 1,
      body: "Export filtered expenses without losing row order.",
    },
  });
  await expect(
    page.getByText("Export filtered expenses without losing row order.", {
      exact: true,
    }),
  ).toBeVisible();
  await openResource(page, "Tasks");
  await page.getByLabel("Filter by milestone").selectOption("");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: /Build CSV serializer/ }).click();
  await expect(page.locator(".task-definition")).toContainText(
    "Verification passes",
  );
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);

  expect(errors).toEqual([]);
});

test("coordinator updates refresh the agreement while project editing retains conflict protection", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  await fixtureMcpProject(call, {
    project_id: "live",
    path: existingDirectory(join(state, "live")),
    name: "Live project",
  });
  await call("create_task", {
    project_id: "live",
    task: {
      stages: fixtureStages(),
      id: "one",
      title: "First task",
      body: "First description",
    },
  });
  await page.goto("/projects/live");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /First task/ })
    .click();
  await expect(page.locator(".task-definition")).toContainText(
    "First description",
  );
  await call("edit_task", {
    project_id: "live",
    task_id: "one",
    changes: { expected_revision: 1, body: "Agent update" },
  });
  await expect(page.locator(".task-definition")).toContainText("Agent update");
  await closeOverlay(page);
  await page.getByLabel("Filter by project").selectOption("live");
  await page
    .getByRole("button", { name: "Project settings", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Edit project", exact: true }),
  ).toBeVisible();
  await ensureEditing(page);
  await page
    .getByRole("textbox", { name: "Description", exact: true })
    .fill("Unsaved focus");
  await call("edit_project", {
    project_id: "live",
    changes: { expected_revision: 1, description: "Agent description" },
  });
  await expect(page.getByText(/A newer revision is available/)).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Description", exact: true }),
  ).toHaveValue("Unsaved focus");
  await page
    .getByRole("button", {
      name: /^(Close editor|Back)$/,
      exact: true,
    })
    .click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Project settings", exact: true })
    .click();
  await ensureEditing(page);
  await page
    .getByRole("textbox", { name: "Description", exact: true })
    .fill("Stale project draft");
  await call("edit_project", {
    project_id: "live",
    changes: {
      expected_revision: 2,
      description: "Another coordinator update",
    },
  });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("stale");
});

test("Conversation permalinks preserve message drafts while coordinator revisions update", async ({
  page,
  request,
}) => {
  const revisionReads: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/revisions/")) revisionReads.push(req.url());
  });
  const call = await connectMcp(request);
  await fixtureMcpProject(call, {
    project_id: "tabs",
    path: existingDirectory(join(state, "tabs")),
  });
  await call("create_task", {
    project_id: "tabs",
    task: {
      stages: fixtureStages(),
      id: "one",
      title: "Tabbed task",
      body: "Original description",
    },
  });
  await page.goto("/projects/tabs");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Tabbed task/ })
    .click();
  await expect(
    page.getByRole("tab", { name: "Task", exact: true }),
  ).toHaveCount(0);
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Unsaved draft");
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  const history = page.getByRole("list", { name: "Task feed" });
  expect(revisionReads).toHaveLength(0);
  await history
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/conversation\/definition%3A1$/);
  await call("edit_task", {
    project_id: "tabs",
    task_id: "one",
    changes: { expected_revision: 1, body: "New saved context" },
  });
  await expect(history).toContainText("Task definition updated");
  expect(revisionReads).toHaveLength(1);
  await history
    .locator('[data-message-id="definition:2"] .activity-entry > summary')
    .click();
  await expect
    .poll(() =>
      history.locator(".text-changes").evaluate((node) => {
        const copy = node.cloneNode(true) as HTMLElement;
        copy.querySelectorAll("del, .sr-only").forEach((el) => el.remove());
        return copy.textContent;
      }),
    )
    .toBe("New saved context");
  expect(revisionReads).toHaveLength(3);
  await expect(history).not.toContainText("Unsaved draft");
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Unsaved draft");
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(page.locator(".task-definition")).toContainText(
    "New saved context",
  );
  await expect(
    page.getByRole("button", { name: "Load latest", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toHaveCount(0);
});

test("archive preserves dated records and supports restore and mobile archiving", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const { workspace_id } = await fixtureMcpProject(call, {
    project_id: "archive-list",
    path: existingDirectory(join(state, "archive-list")),
  });
  await call("create_milestone", {
    workspace_id,
    milestone: { id: "csv", title: "Exports" },
  });
  for (const [id, status, milestone_id] of [
    ["older", "up_next", "csv"],
    ["newer", "done", null],
  ] as const) {
    await call("create_task", {
      project_id: "archive-list",
      task: {
        stages: fixtureStages(),
        id,
        title: `${id} task`,
        status: status === "done" ? "backlog" : status,
        milestone_id,
      },
    });
    if (status === "done")
      await fixtureProgress(request, {
        project_id: "archive-list",
        task_id: id,
        progress: {
          expected_revision: 1,
          status: "done",
          completion: "report",
        },
      });
    await call("edit_task", {
      project_id: "archive-list",
      task_id: id,
      changes: { expected_revision: status === "done" ? 2 : 1, archived: true },
    });
  }
  // Later text edits do not change when the older task was archived.
  await call("edit_task", {
    project_id: "archive-list",
    task_id: "older",
    changes: { expected_revision: 2, body: "Edited after archiving" },
  });
  await page.goto("/projects/archive-list");
  await expect(
    page.getByRole("button", { name: "New task", exact: true }),
  ).toHaveCount(0);
  await closeOverlay(page);
  await openResource(page, "Archive");
  const archive = page.getByRole("region", {
    name: "Archived tasks",
    exact: true,
    includeHidden: true,
  });
  await expect(page.locator(".column")).toHaveCount(0);
  await expect(archive.getByRole("listitem")).toHaveCount(2);
  await expect(archive.getByRole("listitem").first()).toContainText(
    "newer task",
  );
  await expect(
    archive.getByRole("listitem").first().locator(".record-attribution time"),
  ).toBeVisible();
  await page.getByLabel("Filter by milestone").selectOption("csv");
  await expect(archive.getByRole("listitem")).toHaveCount(1);
  await page.getByLabel("Filter by milestone").selectOption("");
  await archive.getByRole("link", { name: /older task/ }).click();
  const info = page.getByRole("region", { name: "Task details", exact: true });
  await expect(
    info.getByRole("img", { name: "Archived", exact: true }),
  ).toBeVisible();
  await expect(
    info.getByRole("heading", { name: /^(Up next|Archived)$/ }),
  ).toHaveCount(0);
  await expect(info.getByText(/Previously|Your agent/)).toHaveCount(0);
  const actions = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    actions.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  await expect(
    actions.getByRole("button", { name: "Restore", exact: true }),
  ).toHaveCount(0);
  await call("edit_task", {
    project_id: "archive-list",
    task_id: "older",
    changes: { expected_revision: 3, archived: false },
  });
  expect(
    (await call("get_task", { project_id: "archive-list", task_id: "older" }))
      .status,
  ).toBe("up_next");
  await closeOverlay(page);
  await openResource(page, "Tasks");
  await page.getByRole("link", { name: /older task/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });

  await expect(
    actions.getByRole("button", { name: "Archive", exact: true }),
  ).toHaveCount(0);
  await call("edit_task", {
    project_id: "archive-list",
    task_id: "older",
    changes: { expected_revision: 4, archived: true },
  });
  await closeOverlay(page);
  await openResource(page, "Archive");
  await expect(archive.getByRole("listitem").first()).toContainText(
    "older task",
  );
});

test("readable conversation preserves message drafts, revision links and legacy paths", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "readable")),
      task_prefix: "REA",
    },
  });
  await request.post("/api/projects/readable/tasks", {
    data: {
      stages: fixtureStages(),
      title: "Readable agreement",
      body: "## Outcome\n\nExport **all columns**.\n\n## Done when\n\n- [ ] Keep row order",
    },
  });
  await page.goto("/projects/readable/tasks/REA-1");
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const thread = editor.getByRole("list", { name: "Task feed" });
  await expect(page.locator(".task-definition .markdown strong")).toHaveText(
    "all columns",
  );
  await expect(
    page.locator(".task-definition").getByRole("checkbox"),
  ).toBeDisabled();
  await expect(editor.getByRole("textbox")).toHaveCount(0);
  await expect(editor.getByRole("textbox")).toHaveCount(0);
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Export **selected columns**.");
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await expect(
    thread
      .locator('[data-kind="definition"]')
      .getByRole("img", { name: "Recorded", exact: true }),
  ).toBeVisible();
  await expect(thread).toContainText("You");
  await thread
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .click();
  await page.goBack();
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Export **selected columns**.");
  const current = await (
    await request.get("/api/projects/readable/tasks/REA-1")
  ).json();
  await request.put("/api/projects/readable/tasks/REA-1", {
    data: {
      expected_revision: current.revision,
      body: "Export **selected columns**.",
    },
  });
  await thread
    .getByRole("link", {
      name: "Permalink: Task definition updated",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/conversation\/definition%3A2$/);
  await expect
    .poll(() =>
      thread.locator(".text-changes").evaluateAll((nodes) =>
        nodes
          .map((node) => {
            const copy = node.cloneNode(true) as HTMLElement;
            copy.querySelectorAll("ins, .sr-only").forEach((el) => el.remove());
            return copy.textContent;
          })
          .join(" "),
      ),
    )
    .toContain("all columns");
  await expect
    .poll(() =>
      thread.locator(".text-changes").evaluateAll((nodes) =>
        nodes
          .map((node) => {
            const copy = node.cloneNode(true) as HTMLElement;
            copy.querySelectorAll("del, .sr-only").forEach((el) => el.remove());
            return copy.textContent;
          })
          .join(" "),
      ),
    )
    .toContain("selected columns");
  const url = page.url();
  await page.reload();
  const other = await page.context().newPage();
  await other.goto(url);
  await expect(other.locator('[data-message-id="definition:2"]')).toBeVisible();
  await other.close();
  await page.goto("/projects/readable/tasks/REA-1/history");
  await expect(thread).toBeVisible();
  await expect(
    editor.getByRole("tab", { name: "Activity", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
});

test("task changes refresh their workspace without reloading the workspace catalog", async ({
  page,
  request,
}) => {
  for (const id of ["scope-one", "scope-two"]) {
    expect(
      (
        await fixtureProject(request, {
          data: {
            id,
            path: existingDirectory(join(state, id)),
            task_prefix: id === "scope-one" ? "SOA" : "SOB",
          },
        })
      ).ok(),
    ).toBe(true);
    expect(
      (
        await request.post(`/api/projects/${id}/tasks`, {
          data: { stages: fixtureStages(), id: "one", title: "Original" },
        })
      ).ok(),
    ).toBe(true);
  }
  const workspace = await projectWorkspace(request, "scope-one");
  await page.goto(`/workspaces/${workspace}/tasks`);
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: /Original/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("status", { name: "Connected", exact: true }),
  ).toBeVisible();
  // Register a second listener before writing so the test knows the event arrived.
  await page.evaluate(
    () =>
      new Promise<void>((ready) => {
        const events = new EventSource("/api/events");
        (window as unknown as { scopedChange: Promise<void> }).scopedChange =
          new Promise<void>((resolve) => {
            events.addEventListener("change", (event) => {
              if (JSON.parse(event.data).projects === null) ready();
              if (JSON.parse(event.data).projects?.includes("scope-two")) {
                events.close();
                resolve();
              }
            });
          });
      }),
  );
  const reads: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "GET" &&
      ["/api/workspaces", `/api/workspaces/${workspace}/view/board`].includes(
        new URL(req.url()).pathname,
      )
    )
      reads.push(req.url());
  });
  expect(
    (
      await request.put("/api/projects/scope-two/tasks/one", {
        data: { expected_revision: 1, title: "Other project change" },
      })
    ).ok(),
  ).toBe(true);
  await page.evaluate(
    () => (window as unknown as { scopedChange: Promise<void> }).scopedChange,
  );
  expect(
    (
      await request.put("/api/projects/scope-one/tasks/one", {
        data: { expected_revision: 1, title: "Relevant change" },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: /Relevant change/ }),
  ).toBeVisible();
  expect(reads).toHaveLength(1);
  expect(reads[0]).toContain(`${workspace}/view/board`);
});

test("publication shares browser, CLI and MCP state without manual editing", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "publication")),
      task_prefix: "PRE",
    },
  });
  await request.post("/api/projects/publication/tasks", {
    data: {
      stages: fixtureStages(),
      id: "export",
      title: "Export JSON",
      status: "up_next",
      body: "Export filtered rows as offline JSON. Test Unicode and empty results.",
    },
  });
  await page.goto("/projects/publication/tasks/PRE-1");
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const draft = page
    .getByRole("region", { name: "Task details", exact: true })
    .getByText("Draft", { exact: true });
  await expect(draft).toBeVisible();
  await draft.focus();
  await expect(page.getByRole("tooltip")).toContainText(
    "Ask the coordinator to prepare this task.",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(editor).toBeVisible();
  await expect(
    editor.getByRole("button", { name: /publish|prepare/i }),
  ).toHaveCount(0);
  const first = cli([
    "task",
    "prepare",
    "PRE-1",
    "--completion",
    "report",
    "--project",
    "publication",
  ]);
  expect(first.publication_status).toBe("published");
  await expect(draft).toHaveCount(0);
  const call = await connectMcp(request);
  await call("add_activity", {
    project_id: "publication",
    entry: { task_id: "PRE-1", body: "The sample has many Unicode titles." },
  });
  await expect(draft).toHaveCount(0);
  await call("edit_task", {
    project_id: "publication",
    task_id: "PRE-1",
    changes: {
      expected_revision: first.revision,
      stages: {
        expected_revision: 1,
        stages: fixtureStages(),
        reason: "Reconcile export scope",
      },
      body: "Export filtered rows as JSON, capped at 500. Test Unicode, empty output and overflow.",
    },
  });
  await expect(draft).toBeVisible();
  const fromCli = cli([
    "task",
    "prepare",
    "PRE-1",
    "--completion",
    "report",
    "--project",
    "publication",
  ]);
  expect(fromCli.publication_status).toBe("published");
  await expect(draft).toHaveCount(0);
  const plan = await call("get_task_stages", {
    project_id: "publication",
    task_id: "PRE-1",
  });
  await call("edit_task", {
    project_id: "publication",
    task_id: "PRE-1",
    changes: {
      expected_revision: fromCli.revision,
      body: "Export filtered rows as JSON, capped at 500. Test Unicode, empty output and overflow. Use UTF-8.",
      stages: {
        expected_revision: plan.revision,
        stages: fixtureStages(),
        reason: "Cover the updated encoding requirement",
      },
    },
  });
  await expect(draft).toBeVisible();
  const current = await call("get_task", {
    project_id: "publication",
    task_id: "PRE-1",
  });
  await call("edit_task", {
    project_id: "publication",
    task_id: "PRE-1",
    changes: {
      preparation: { completion: "report" },
      expected_revision: current.revision,
    },
  });
  await page.reload();
  await expect(draft).toHaveCount(0);
  await expect(editor).toContainText("capped at 500");
});

test("conversation capture prepares atomically while task defaults show only useful work", async ({
  page,
  request,
}) => {
  const project_id = "conversation-capture";
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "CAP",
    },
  });
  await page.goto(`/projects/${project_id}`);
  await expect(
    page.getByRole("link", { name: "Set up your coordinator" }),
  ).toHaveCount(0);
  const call = await connectMcp(request);
  await call("get_board", { project_id });
  const task = await call("create_task", {
    project_id,
    task: {
      stages: [
        {
          id: "investigate",
          title: "Investigate",
          outcome: "Compare available formats",
          status: "planned",
        },
        {
          id: "report",
          title: "Report",
          outcome: "Present findings",
          status: "planned",
        },
      ],
      title: "Investigate import formats",
      body: "Compare existing formats, evidence and a recommendation; deliver a report without changing repository files.",
      labels: ["investigation"],
      preparation: {
        completion: "report",
      },
    },
  });
  expect(task.publication_status).toBe("published");
  expect(task.status).toBe("backlog");
  expect(
    (
      await call("get_workspace_workers", {
        workspace_id: await projectWorkspace(request, project_id),
      })
    ).enabled,
  ).toBe(false);
  await page.goto(`/projects/${project_id}/tasks/CAP-1`);
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  for (const text of [
    "No prerequisites.",
    "No dependent tasks.",
    "No dependency or input blockers.",
    "DRAFT",
  ])
    await expect(editor.getByText(text, { exact: true })).toHaveCount(0);
  await expect(editor.getByText("Related work", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    editor.getByRole("heading", { name: "Waiting on", exact: true }),
  ).toHaveCount(0);
  const planTask = await call("get_task", { project_id, task_id: task.id });
  const stages = page.getByRole("group", {
    name: "Stages at this update",
    exact: true,
  });
  await expect(page.getByRole("list", { name: "Task feed" })).toHaveCount(0);
  await expect(stages).not.toBeVisible();
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await page
    .getByRole("link", { name: "Permalink: Stages defined", exact: true })
    .click();
  await expect(stages.getByRole("link")).toHaveCount(0);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  // Stage details are disclosed independently from the default outcome.
  await expect(stages).toBeVisible();
  await stages.locator(".task-stage").first().focus();
  // Exercise keyboard focus on the visible stage sequence.
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("tooltip")).toContainText(
    "Compare available formats",
  );
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await page
    .getByRole("link", { name: "Permalink: Stages defined", exact: true })
    .click();
  await expect(page).toHaveURL(/conversation\/plan%3A1$/);
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "Initial task plan",
  );
  const stageSnapshot = await call("get_task_stages", {
    project_id,
    task_id: task.id,
  });
  await call("update_task_stages", {
    project_id,
    task_id: task.id,
    request: {
      expected_revision: stageSnapshot.revision,
      agreement_revision: planTask.agreement_revision,
      stages: stageSnapshot.stages.map((stage: { id: string }) => ({
        ...stage,
        status: stage.id === "investigate" ? "completed" : "active",
      })),
      reason: "Evidence gathered; writing findings.",
    },
  });
  const changedStages = page.locator('[data-message-id="plan:2"]');
  await expect(changedStages).toContainText("Stage changed");
  await expect(changedStages).not.toContainText(
    "Investigate: Planned → Completed.",
  );
  await expect(changedStages.locator('[aria-current="step"]')).toHaveText(
    "Report: Active",
  );
  await expect(
    page.locator('[data-message-id="plan:1"] [aria-current="step"]'),
  ).toHaveCount(0);
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "Investigate: Planned",
  );
  await page.reload();
  await expect(changedStages.locator('[aria-current="step"]')).toHaveText(
    "Report: Active",
  );
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "Investigate: Planned",
  );
  const current = await call("get_task", { project_id, task_id: task.id });
  const updated = await call("edit_task", {
    project_id,
    task_id: task.id,
    changes: {
      expected_revision: current.revision,
      stages: {
        expected_revision: 2,
        stages: stageSnapshot.stages,
        reason: "Reconcile clarified outcome",
      },
      body: "Compare formats, include uncertainty and recommend one; report only with no file changes.",
      preparation: {
        completion: "report",
      },
    },
  });
  expect(updated.publication_status).toBe("published");
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(
    editor
      .getByLabel("Current task definition")
      .getByText(/Compare formats, include uncertainty/),
  ).toBeVisible();
  const stale = await request.put(
    `/api/projects/${project_id}/tasks/${task.id}`,
    {
      data: {
        expected_revision: current.revision,
        body: "Stale replacement",
        preparation: {
          completion: "report",
        },
      },
    },
  );
  expect(stale.status()).toBe(409);
  const related = await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      title: "Follow findings",
      dependencies: [task.id],
    },
  });
  await expect(editor.getByText("Related work", { exact: true })).toHaveCount(
    0,
  );
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(editor.getByText("Needed by:", { exact: false })).toBeVisible();
  await expect(
    editor.getByRole("link", { name: related.key, exact: true }),
  ).toBeVisible();
  await expect(
    editor.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await call("get_workspace_workers", {
        workspace_id: await projectWorkspace(request, project_id),
      })
    ).enabled,
  ).toBe(false);
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(
    editor
      .getByLabel("Current task definition")
      .getByText(/Compare formats, include uncertainty/),
  ).toBeVisible();
});

test("attempt activity updates without reloading the board and replays on reload", async ({
  page,
}, testInfo) => {
  const fixture = async (op: string) => {
    const value = JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "--project",
          checkout,
          "python",
          join(checkout, "tests/activity_browser_fixture.py"),
          state,
          op,
        ],
        { encoding: "utf8" },
      ),
    );
    // The fixture process writes durable state without the service callback. Deliver
    // its focused notification explicitly; backend tests verify publication/coalescing.
    if (op !== "create")
      await page.evaluate(
        (attempt) =>
          window.dispatchEvent(
            new CustomEvent("flowfield:activity", {
              detail: { activity: [["stream-project", attempt]] },
            }),
          ),
        value.run_id,
      );
    return value;
  };
  // This fixture publishes focused activity directly below. Exclude unrelated
  // service-wide SSE changes from other journeys during the no-board-read assertion.
  await page.route("**/api/events", (route) => route.abort());
  let releasePoll!: () => void;
  const pollReady = new Promise<void>((resolve) => {
    releasePoll = resolve;
  });
  await page.route("**/api/announcements/poll", async (route) => {
    await pollReady;
    await route.continue();
  });
  const seeded = await fixture("create");
  await page.goto("/projects/stream-project/tasks/STR-1/history");
  const output = page.getByRole("region", {
    name: "Worker activity",
    exact: true,
  });
  await expect(output).toContainText("create observed output");
  await expect(
    page.getByText("Token usage not reported", { exact: true }),
  ).toHaveCount(0);
  const currentTokens = page.locator(".run-activity .activity-context");
  await expect(currentTokens).toContainText("Current tokens");
  await expect(
    currentTokens.getByLabel("12% context used", { exact: true }),
  ).toBeVisible();
  const outputBox = (await output.boundingBox())!;
  expect((await currentTokens.boundingBox())!.y).toBeGreaterThanOrEqual(
    outputBox.y + outputBox.height,
  );
  await page.screenshot({
    path: testInfo.outputPath("worker-current-tokens.png"),
  });
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("textbox"),
  ).toHaveCount(0);
  let boards = 0;
  page.on("request", (req) => {
    if (req.url().endsWith("/view/board")) boards++;
  });
  // Complete a real notification delivery poll inside the measured window.
  // It must not masquerade as a workspace change or reload the board.
  const polled = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/announcements/poll") &&
      response.status() === 200,
  );
  releasePoll();
  await polled;
  await fixture("later");
  await expect(output).toContainText("later observed output");
  expect(boards).toBe(0);
  await page.reload();
  await expect(output).toContainText("create observed output");
  await expect(output).toContainText("later observed output");
  await expect(page.locator(".task-activity-item").last()).toHaveAttribute(
    "data-message-id",
    `attempt:${seeded.run_id}`,
  );
  await expect(page.locator(".entity-pane-title .work-state")).toHaveCount(1);
  await closeOverlay(page);
  const activeCard = page
    .locator(".task-card")
    .filter({ hasText: "Observe live output" });
  await expect(activeCard).toContainText("Working");
  const dot = activeCard.locator(".work-state-dot");
  const pulse = await dot.evaluate((el) => {
    const animation = el.getAnimations()[0];
    animation.pause();
    animation.currentTime = 0;
    const low = Number(getComputedStyle(el).opacity);
    animation.currentTime = 900;
    const high = Number(getComputedStyle(el).opacity);
    const glow = getComputedStyle(el).boxShadow;
    animation.play();
    return { low, high, glow };
  });
  expect(pulse.high - pulse.low).toBeGreaterThan(0.4);
  expect(pulse.glow).not.toBe("none");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(dot).toHaveCSS("animation-name", "none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/projects/stream-project/tasks/STR-1/history");
  await expect(output).toContainText("later observed output");
  await fixture("long");
  await expect(output).toContainText("long observed output");
  const atEnd = () =>
    output.evaluate(
      (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3,
    );
  await expect.poll(atEnd).toBe(true);
  await output.evaluate((el) => el.scrollTo({ top: el.scrollHeight / 3 }));
  await expect.poll(atEnd).toBe(false);
  const before = await output.evaluate((el) => el.scrollTop);
  await fixture("while-reading");
  await expect(output).toContainText("while-reading observed output");
  await expect.poll(() => output.evaluate((el) => el.scrollTop)).toBe(before);
  await output.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
  await expect.poll(atEnd).toBe(true);
  await fixture("following-again");
  await expect(output).toContainText("following-again observed output");
  await expect.poll(atEnd).toBe(true);
  await expect(
    page.getByRole("button", { name: "Follow latest activity" }),
  ).toHaveCount(0);
  await fixture("compact");
  const script = output.locator('[data-kind="command"]');
  await expect(script).toContainText("Exit code: 17");
  await expect(script.locator("summary").first()).not.toContainText(
    "retained source",
  );
  await script.locator("summary").first().click();
  await expect(script.locator("details pre")).toContainText("retained source");
  await expect(output.locator('[data-kind="agent"] summary')).toContainText(
    "Code block collapsed",
  );
  await expect(page.getByLabel("Reported token usage").first()).toContainText(
    "240 tokens reported so far · 100 cached input",
  );
  const attempt = page.locator('[data-kind="attempt"]');
  await expect(
    attempt.getByText("Execution details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    attempt.getByRole("region", { name: "Execution details", exact: true }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(/STR-1\/history$/);
  await page.goto(
    "/projects/stream-project/tasks/STR-1/history/worker:" + seeded.run_id,
  );
  await expect(
    attempt.getByRole("region", { name: "Execution details", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stop worker", exact: true }).click();
  const stopConfirmation = page.getByRole("alertdialog", {
    name: "Stop this worker?",
    exact: true,
  });
  await expect(stopConfirmation).toBeVisible();
  await stopConfirmation
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Stop worker", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Retry worker", exact: true }),
  ).toHaveCount(0);
  await page.route(
    `**/api/projects/stream-project/runs/${seeded.run_id}/stop`,
    async (route) => {
      await fixture("stop-race"); // Progress arrives after confirmation serialized its attempt revision.
      await route.continue();
    },
  );
  await page.getByRole("button", { name: "Stop worker", exact: true }).click();
  await stopConfirmation
    .getByRole("button", { name: "Stop worker", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry worker", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Archive", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Retry worker", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Retry worker", exact: true }),
  ).toHaveCount(0);
  await page.reload();

  await expect(
    page.getByRole("button", { name: "Retry worker", exact: true }),
  ).toHaveCount(0);
  await closeOverlay(page);
  await openResource(page, "Tasks");
  await expect(page.locator(".up_next")).toContainText("Observe live output");
  await expect(page.locator(".up_next")).toContainText("Queue paused");
});

test("task feed follows the live end but preserves reading position and timestamp targets", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "feed-reading")),
      task_prefix: "FED",
    },
  });
  const path = "/api/projects/feed-reading";
  await request.post(`${path}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "one",
      title: "Read a growing feed",
      body: "Keep the reader oriented.",
    },
  });
  for (let i = 0; i < 12; i++) {
    await request.post(`${path}/activity`, {
      data: {
        task_id: "one",
        body: `Observation ${i}\n\n${"Useful detail. ".repeat(40)}`,
      },
    });
  }
  await page.goto("/projects/feed-reading/tasks/FED-1/history");
  const pane = page.locator(".entity-pane-body");
  const feed = page.getByRole("list", { name: "Task feed" });
  const atBottom = () =>
    pane.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3);
  await expect(feed).toContainText("Observation 11");
  await expect.poll(atBottom).toBe(true);
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest while following" },
  });
  await expect(feed).toContainText("Newest while following");
  await expect.poll(atBottom).toBe(true);
  // Expand retained notes so this still exercises reading long evidence.
  for (const row of await feed
    .locator('[data-kind="activity"] .activity-entry > summary')
    .all())
    await row.click();
  const anchor = feed.locator('[data-kind="activity"]').nth(5);
  await anchor.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await expect.poll(atBottom).toBe(false);
  const position = () =>
    anchor.evaluate(
      (el) =>
        el.getBoundingClientRect().top -
        el.closest(".entity-pane-body")!.getBoundingClientRect().top,
    );
  const before = await position();
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest while reading earlier work" },
  });
  await expect(feed).toContainText("Newest while reading earlier work");
  // This checks scroll anchoring, not a fixed layout or pixel styling contract.
  await expect
    .poll(async () => Math.abs((await position()) - before) < 2)
    .toBe(true);
  await expect.poll(atBottom).toBe(false);
  await openResource(page, "Documents");
  await page.getByRole("tab", { name: /FED-1 Read a growing feed/ }).click();
  await expect(feed).toContainText("Newest while reading earlier work");
  await expect
    .poll(async () => Math.abs((await position()) - before) < 2)
    .toBe(true);
  await expect.poll(atBottom).toBe(false);
  await pane.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect.poll(atBottom).toBe(true);
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest after returning to the bottom" },
  });
  await expect(feed).toContainText("Newest after returning to the bottom");
  await expect.poll(atBottom).toBe(true);
  const stamp = anchor.getByRole("link", {
    name: "Permalink: Note",
    exact: true,
  });
  const href = await stamp.getAttribute("href");
  await page.goto(href!);
  await expect(anchor).toBeInViewport();
  await expect(
    page.getByRole("button", {
      name: /^(Close editor|Back)$/,
      exact: true,
    }),
  ).toBeInViewport();
  await expect(feed.locator(".detail-entry-title a")).toHaveCount(0);
});

test("legacy task-question links resolve into the feed and preserve answers", async ({
  page,
  request,
}, testInfo) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "feed-question")),
      task_prefix: "FQN",
    },
  });
  const path = "/api/projects/feed-question";
  await request.post(`${path}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "one",
      title: "A question in its task",
    },
  });
  await request.post(`${path}/questions`, {
    data: {
      id: "choice",
      task_id: "one",
      question: "Which format?",
      context: "Choose an output.",
      recommendation: "JSON",
      choices: ["JSON", "CSV"],
      blocking_scope: "Format",
    },
  });
  await page.goto("/projects/feed-question/inbox/choice");
  await expect(page).toHaveURL(
    /tasks\/FQN-1\/conversation\/question%3Achoice$/,
  );
  await expect(
    page.getByRole("dialog", { name: "Question", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Waiting on", exact: true }),
  ).toHaveCount(0);
  const answer = page.getByRole("textbox", {
    name: "Your answer",
    exact: true,
  });
  await expect(answer).toHaveAttribute("placeholder", "Your answer…");
  const input = page.getByRole("form", { name: "Task input", exact: true });
  await expect(input).toContainText("Answer the question");
  await expect(
    input.getByRole("button", { name: "Worker settings", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-question-desktop-light.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(answer).toBeVisible();
  await expect(input).toContainText("Answer the question");
  await page.screenshot({
    path: testInfo.outputPath("coordinator-question-narrow-dark.png"),
  });
  await expect(
    page.getByRole("button", { name: "JSON", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "CSV", exact: true }),
  ).toBeVisible();
  await answer.fill("JSON");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .locator('[data-kind="answer"]'),
  ).toContainText("JSON");
  const question = await (await request.get(`${path}/questions/choice`)).json();
  expect(question.answer).toBe("JSON");
  await page.goto(
    "/projects/feed-question/tasks/FQN-1/related/questions/choice",
  );
  await expect(page).toHaveURL(/conversation\/question%3Achoice$/);
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .locator('[data-kind="answer"]'),
  ).toContainText("JSON");
});
