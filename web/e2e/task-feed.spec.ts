import { openResource } from "./shell-navigation";
import {
  projectWorkspace,
  fixtureProject,
  fixtureMcpProject,
  fixtureProgress,
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
        },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post(
        `/api/workspaces/${await projectWorkspace(page.request, "board-recovery")}/tasks`,
        {
          data: { title: "Preserved board work" },
        },
      )
    ).ok(),
  ).toBe(true);
  let fail = true;
  const workspace = await projectWorkspace(page.request, "board-recovery");
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "board-recovery")}/tasks`,
  );
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
      await request.post(
        `/api/workspaces/${await projectWorkspace(page.request, "board-recovery")}/tasks`,
        {
          data: { title: "New work after recovery" },
        },
      )
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
    workspace_id: await projectWorkspace(page.request, "live"),
    task: {
      project_id: "live",
      id: "one",
      title: "First task",
      body: "First description",
    },
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "live")}/tasks`,
  );
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /First task/ })
    .click();
  await expect(page.locator(".task-definition")).toContainText(
    "First description",
  );
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "live"),
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

test("Feed disclosures preserve message drafts while coordinator revisions update", async ({
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
    workspace_id: await projectWorkspace(page.request, "tabs"),
    task: {
      project_id: "tabs",
      id: "one",
      title: "Tabbed task",
      body: "Original description",
    },
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "tabs")}/tasks`,
  );
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /Tabbed task/ })
    .click();
  await expect(
    page.getByRole("button", { name: "Task settings", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Unsaved draft");
  const history = page.getByRole("list", { name: "Task feed" });
  expect(revisionReads).toHaveLength(0);
  await history.locator('[data-message-id="definition:1"] summary').focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/tasks\/T-1$/);
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "tabs"),
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
  await fixtureMcpProject(call, {
    project_id: "archive-list",
    path: existingDirectory(join(state, "archive-list")),
  });
  for (const [id, status] of [
    ["older", "up_next"],
    ["newer", "done"],
  ] as const) {
    await call("create_task", {
      workspace_id: await projectWorkspace(page.request, "archive-list"),
      task: {
        project_id: "archive-list",
        id,
        title: `${id} task`,
        status: status === "done" ? "backlog" : status,
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
      workspace_id: await projectWorkspace(page.request, "archive-list"),
      task_id: id,
      changes: {
        expected_revision: (
          await call("get_task", {
            workspace_id: await projectWorkspace(request, "archive-list"),
            task_id: id,
          })
        ).revision,
        archived: true,
      },
    });
  }
  // Later text edits do not change when the older task was archived.
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "archive-list"),
    task_id: "older",
    changes: { expected_revision: 2, body: "Edited after archiving" },
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "archive-list")}/tasks`,
  );
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
    workspace_id: await projectWorkspace(page.request, "archive-list"),
    task_id: "older",
    changes: { expected_revision: 3, archived: false },
  });
  expect(
    (
      await call("get_task", {
        workspace_id: await projectWorkspace(page.request, "archive-list"),
        task_id: "older",
      })
    ).status,
  ).toBe("up_next");
  await closeOverlay(page);
  await openResource(page, "Task Board");
  await page
    .getByTestId("work")
    .getByRole("link", { name: /older task/ })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });

  await expect(
    actions.getByRole("button", { name: "Archive", exact: true }),
  ).toHaveCount(0);
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "archive-list"),
    task_id: "older",
    changes: { expected_revision: 4, archived: true },
  });
  await closeOverlay(page);
  await openResource(page, "Archive");
  await expect(archive.getByRole("listitem").first()).toContainText(
    "older task",
  );
});

test("publication shares browser, CLI and MCP state without manual editing", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "publication")),
    },
  });
  await request.post(
    `/api/workspaces/${await projectWorkspace(page.request, "publication")}/tasks`,
    {
      data: {
        id: "export",
        title: "Export JSON",
        status: "up_next",
        body: "Export filtered rows as offline JSON. Test Unicode and empty results.",
      },
    },
  );
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "publication")}/tasks/T-1`,
  );
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
    "T-1",
    "--completion",
    "report",
    "--workspace",
    await projectWorkspace(request, "publication"),
  ]);
  expect(first.publication_status).toBe("published");
  await expect(draft).toHaveCount(0);
  const call = await connectMcp(request);
  await call("add_activity", {
    workspace_id: await projectWorkspace(page.request, "publication"),
    entry: { task_id: "T-1", body: "The sample has many Unicode titles." },
  });
  await expect(draft).toHaveCount(0);
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "publication"),
    task_id: "T-1",
    changes: {
      expected_revision: first.revision,

      body: "Export filtered rows as JSON, capped at 500. Test Unicode, empty output and overflow.",
    },
  });
  await expect(draft).toBeVisible();
  const fromCli = cli([
    "task",
    "prepare",
    "T-1",
    "--completion",
    "report",
    "--workspace",
    await projectWorkspace(request, "publication"),
  ]);
  expect(fromCli.publication_status).toBe("published");
  await expect(draft).toHaveCount(0);
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "publication"),
    task_id: "T-1",
    changes: {
      expected_revision: fromCli.revision,
      body: "Export filtered rows as JSON, capped at 500. Test Unicode, empty output and overflow. Use UTF-8.",
    },
  });
  await expect(draft).toBeVisible();
  const current = await call("get_task", {
    workspace_id: await projectWorkspace(page.request, "publication"),
    task_id: "T-1",
  });
  await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, "publication"),
    task_id: "T-1",
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
    },
  });
  await page.goto(`/projects/${project_id}`);
  await expect(
    page.getByRole("link", { name: "Set up your coordinator" }),
  ).toHaveCount(0);
  const call = await connectMcp(request);
  await call("get_board", {
    workspace_id: await projectWorkspace(page.request, project_id),
  });
  const task = await call("create_task", {
    workspace_id: await projectWorkspace(page.request, project_id),
    task: {
      project_id: project_id,
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
        workspace_id: await projectWorkspace(page.request, project_id),
      })
    ).enabled,
  ).toBe(false);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project_id)}/tasks/T-1`,
  );
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
  const current = await call("get_task", {
    workspace_id: await projectWorkspace(page.request, project_id),
    task_id: task.id,
  });
  const updated = await call("edit_task", {
    workspace_id: await projectWorkspace(page.request, project_id),
    task_id: task.id,
    changes: {
      expected_revision: current.revision,

      body: "Compare formats, include uncertainty and recommend one; report only with no file changes.",
      preparation: {
        completion: "report",
      },
    },
  });
  expect(updated.publication_status).toBe("published");
  await expect(
    editor
      .getByLabel("Current task definition")
      .getByText(/Compare formats, include uncertainty/),
  ).toBeVisible();
  const stale = await request.put(
    `/api/workspaces/${await projectWorkspace(page.request, project_id)}/tasks/${task.id}`,
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
    workspace_id: await projectWorkspace(page.request, project_id),
    task: {
      project_id: project_id,
      title: "Follow findings",
      dependencies: [task.id],
    },
  });
  await expect(editor.getByText("Related work", { exact: true })).toHaveCount(
    0,
  );
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
        workspace_id: await projectWorkspace(page.request, project_id),
      })
    ).enabled,
  ).toBe(false);
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
  // Live output, scroll/reload behavior and stopped-worker recovery each have
  // their own bounded assertions; allow enough time for the complete journey.
  test.setTimeout(60_000);
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "stream-project")}/tasks/T-1/history`,
  );
  const output = page.getByRole("region", {
    name: "Worker activity",
    exact: true,
  });
  const activeEntry = page.locator(
    `[data-message-id="attempt:${seeded.run_id}"]`,
  );
  await expect(
    activeEntry.locator("details.activity-entry"),
  ).not.toHaveAttribute("open");
  await expect(output).toHaveCount(0);
  await activeEntry.locator(".activity-entry-state").click();
  await expect(output).toContainText("create observed output");
  await expect(
    page.getByText("Token usage not reported", { exact: true }),
  ).toHaveCount(0);
  const currentTokens = page.locator(".run-activity .activity-context");
  await expect(currentTokens).toContainText("12,345 / 100,000 context tokens");
  await expect(
    currentTokens.getByLabel("12% context used", { exact: true }),
  ).toBeVisible();
  await currentTokens.getByLabel("12% context used", { exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
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
  await expect(
    activeEntry.locator("details.activity-entry"),
  ).not.toHaveAttribute("open");
  await activeEntry.locator(".activity-entry-state").click();
  await expect(output).toContainText("create observed output");
  await expect(output).toContainText("later observed output");
  await expect(page.locator(".task-activity-item").last()).toHaveAttribute(
    "data-message-id",
    `attempt:${seeded.run_id}`,
  );
  await expect(page.locator(".editor-heading .work-state")).toHaveCount(1);
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "stream-project")}/tasks/T-1/history`,
  );
  await activeEntry.locator(".activity-entry-state").click();
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
  await activeEntry.locator(".activity-entry-state").click();
  await activeEntry.locator(".activity-entry-state").click();
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
  await expect(page).toHaveURL(/T-1\/history$/);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "stream-project")}/tasks/T-1/history/worker:` +
      seeded.run_id,
  );
  await expect(
    attempt.getByRole("region", { name: "Execution details", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stop task", exact: true }),
  ).toHaveCount(0);
  const workspace = await projectWorkspace(page.request, "stream-project");
  const task = await (
    await page.request.get(`/api/workspaces/${workspace}/tasks/stream`)
  ).json();
  await fixture("stop-race");
  const stopped = await page.request.post(
    `/api/workspaces/${workspace}/tasks/stream/stop`,
    { data: { expected_revision: task.revision } },
  );
  expect(stopped.ok()).toBe(true);
  await page.goto(`/workspaces/${workspace}/requests/attempt/${seeded.run_id}`);
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
  await openResource(page, "Task Board");
  await expect(page.locator(".up_next")).toContainText("Observe live output");
  await expect(page.locator(".up_next")).toContainText("Queue paused");
});

test("task feed stays static on opening and updates and restores explicit tab position", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "feed-reading")),
    },
  });
  const path = `/api/workspaces/${await projectWorkspace(page.request, "feed-reading")}`;
  await request.post(`${path}/tasks`, {
    data: {
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "feed-reading")}/tasks/T-1/history`,
  );
  const pane = page.locator(".entity-pane-body");
  const feed = page.getByRole("list", { name: "Task feed" });
  const atBottom = () =>
    pane.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3);
  await expect(feed).toContainText("Observation 11");
  await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBe(0);
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest while following" },
  });
  await expect(feed).toContainText("Newest while following");
  await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBe(0);
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
  await page.getByRole("tab", { name: /T-1 Read a growing feed/ }).click();
  await expect(feed).toContainText("Newest while reading earlier work");
  await expect
    .poll(async () => Math.abs((await position()) - before) < 2)
    .toBe(true);
  await expect.poll(atBottom).toBe(false);
  await pane.hover();
  await page.mouse.wheel(0, 10000);
  await expect.poll(atBottom).toBe(true);
  const bottomPosition = await pane.evaluate((el) => el.scrollTop);
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest after returning to the bottom" },
  });
  await expect(feed).toContainText("Newest after returning to the bottom");
  await expect
    .poll(() => pane.evaluate((el) => el.scrollTop))
    .toBe(bottomPosition);
  await expect.poll(atBottom).toBe(false);
  await expect(feed.getByRole("link", { name: /^Permalink:/ })).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: /^(Close editor|Back)$/,
      exact: true,
    }),
  ).toBeInViewport();
  await expect(feed.locator(".detail-entry-title a")).toHaveCount(0);
});
