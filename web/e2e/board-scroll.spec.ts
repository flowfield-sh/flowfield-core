import {
  fixtureProject,
  projectWorkspace,
  test,
  existingDirectory,
  state,
  fixtureStages,
} from "./support";
import { join } from "node:path";
import { expect } from "@playwright/test";

test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } });

test("board columns scroll independently with fixed headings and reachable cards", async ({
  page,
  request,
}, testInfo) => {
  const project = "column-scroll";
  expect(
    (
      await fixtureProject(request, {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "BSC",
        },
      })
    ).ok(),
  ).toBe(true);
  for (const status of ["backlog", "up_next"]) {
    for (let i = 0; i < 14; i++) {
      expect(
        (
          await request.post(`/api/projects/${project}/tasks`, {
            data: {
              stages: fixtureStages(),
              title: `${status} task ${i}`,
              status,
            },
          })
        ).ok(),
      ).toBe(true);
    }
  }
  const workspace = await projectWorkspace(request, project);
  // Report occupancy without starting a model or changing the paused queue.
  await page.route(
    `**/api/workspaces/${workspace}/workers/occupancy`,
    (route) => route.fulfill({ json: { active: 1, uncertain: 0 } }),
  );
  await page.route(`**/api/workspaces/${workspace}/workers`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: { ...(await response.json()), max_parallel: 2 },
    });
  });
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.goto(`/projects/${project}`);
  const board = page.locator(".board");
  const backlog = page.getByRole("region", {
    name: "Backlog tasks",
    exact: true,
  });
  const upNext = page.getByRole("region", {
    name: "Up next tasks",
    exact: true,
  });
  await expect(page.locator(".queue-controls")).toContainText(
    "Queue paused · 1/2 active",
  );
  await expect(backlog.getByRole("link")).toHaveCount(14);
  // Force scrollbars that consume layout space, even on an overlay-scrollbar host.
  await page.addStyleTag({
    content: `* { scrollbar-width: auto; }
      ::-webkit-scrollbar { width: 14px; height: 14px; }`,
  });
  expect(
    await board.evaluate(
      (node) => (node as HTMLElement).offsetHeight - node.clientHeight,
    ),
  ).toBeGreaterThan(0);
  const headers = page.locator(".board .column > h2");
  const positions = () =>
    headers.evaluateAll((nodes) =>
      nodes.map((node) => node.getBoundingClientRect().top),
    );
  const initial = await positions();
  await backlog.focus();
  await page.keyboard.press("End");
  await expect
    .poll(() => backlog.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  await expect(
    backlog.getByRole("link", { name: /backlog task 13$/ }),
  ).toBeInViewport();
  expect(await upNext.evaluate((node) => node.scrollTop)).toBe(0);
  expect(await positions()).toEqual(initial);
  expect(
    await board.evaluate((node) =>
      [...node.querySelectorAll(".column")].every(
        (column) =>
          column.getBoundingClientRect().bottom <=
          node.getBoundingClientRect().top + node.clientHeight,
      ),
    ),
  ).toBe(true);
  await upNext.hover();
  await page.mouse.wheel(0, 700);
  await expect
    .poll(() => upNext.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  expect(await positions()).toEqual(initial);
  for (const selector of [".workspace-work-content", ".board", "html"]) {
    expect(
      await page
        .locator(selector)
        .evaluate((node) => node.scrollHeight <= node.clientHeight),
    ).toBe(true);
  }
  await page.screenshot({
    path: testInfo.outputPath("board-columns-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 640 });
  await expect
    .poll(() => board.evaluate((node) => node.scrollWidth > node.clientWidth))
    .toBe(true);
  await board.evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
  });
  await expect(
    page.getByText("Finished outcomes", { exact: true }),
  ).toBeInViewport();
  expect(
    await board.evaluate((node) => node.scrollHeight <= node.clientHeight),
  ).toBe(true);
  expect(
    await page
      .locator(".workspace-work-content")
      .evaluate((node) => node.scrollHeight <= node.clientHeight),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("board-columns-mobile.png"),
  });
});

test("Inbox response lists paginate and wrap long cards without horizontal scrolling", async ({
  page,
  request,
}, testInfo) => {
  const project = "attention-scroll";
  expect(
    (
      await fixtureProject(request, {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "ASC",
        },
      })
    ).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, project);
  await page.route(
    `**/api/workspaces/${workspace}/view/attention?*`,
    (route) => {
      const query = new URL(route.request().url()).searchParams;
      const column = query.get("column")!;
      const offset = Number(query.get("offset") ?? 0);
      return route.fulfill({
        json: {
          items: Array.from({ length: offset ? 1 : 14 }, (_, i) => ({
            id: `${column}-${offset + i}`,
            project_id:
              column === "action" && offset + i === 0 ? null : project,
            kind:
              column === "action" && offset + i === 0
                ? "permission"
                : "question",
            task_key: null,
            title: `${column} item ${offset + i} ${"long-unbroken-title".repeat(15)}`,
            status:
              column === "action"
                ? "open"
                : column === "waiting"
                  ? "answered"
                  : "applied",
            updated_at: "2026-10-09T12:00:00Z",
          })),
          total: 15,
          next_offset: offset ? null : 14,
        },
      });
    },
  );
  await page.route(
    `**/api/workspaces/${workspace}/permissions/action-0?*`,
    (route) =>
      route.fulfill({
        json: {
          id: "action-0",
          project_id: null,
          workspace_id: workspace,
          role: "coordinator",
          task_id: null,
          run_id: null,
          conversation_id: "fixture",
          binding: "fixture",
          turn_id: "fixture",
          tool_id: "fixture",
          title: "tool_".repeat(100),
          details: "argument_".repeat(200),
          options: [
            { id: "allow", label: "Allow_".repeat(80), kind: "allow_once" },
          ],
          revision: 1,
          status: "pending",
          answer: null,
          created_at: "2026-10-09T12:00:00Z",
          updated_at: "2026-10-09T12:00:00Z",
          expires_at: null,
          released_at: null,
        },
      }),
  );
  await page.route(`**/api/projects/${project}/questions/*`, (route) =>
    route.fulfill({
      json: {
        id: new URL(route.request().url()).pathname.split("/").at(-1),
        revision: 1,
        question: "Choose " + "long-unbroken-title".repeat(15),
        context: "Explain the required choice.",
        recommendation: "Choose the first option.",
        choices: ["First option", "Second option"],
        status: "open",
        task_id: null,
        task_key: null,
        affected_task_keys: [],
        answer_count: 0,
      },
    }),
  );
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.goto(`/projects/${project}/inbox`);
  const action = page.getByRole("tabpanel", { name: "Needs me", exact: true });
  await expect(action.locator(".request-card")).toHaveCount(14);
  await action.getByRole("button", { name: "Load more", exact: true }).click();
  await expect(action.locator(".request-card")).toHaveCount(15);
  await action.locator(".request-card").last().scrollIntoViewIfNeeded();
  await expect(action.locator(".request-card").last()).toBeInViewport();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(
    page.getByRole("tabpanel", { name: "History", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Needs me", exact: true }).click();
  await expect(
    action.getByRole("button", { name: "Allow_".repeat(80), exact: true }),
  ).toBeVisible();
  for (const width of [1800, 390]) {
    await page.setViewportSize({ width, height: 800 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(
      await action.evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`inbox-list-${width}.png`),
    });
  }
  await page.getByRole("tab", { name: "Needs me", exact: true }).click();
  await expect(
    page.getByRole("group", { name: "Suggested answers" }).first(),
  ).toBeVisible();
  expect(
    await action.evaluate((node) => node.scrollWidth <= node.clientWidth),
  ).toBe(true);
});

test("milestones share board scrolling, compact controls and URL views", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: { name: "Milestone scrolling" },
    })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  for (let index = 0; index < 16; index++) {
    expect(
      (
        await request.post(`/api${base}/milestones`, {
          data: { title: `Milestone ${index}`, body: "Shared release scope" },
        })
      ).ok(),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 760 });
  await page.goto(`${base}/milestones`);
  const planned = page.getByRole("region", {
    name: "Planned milestones",
    exact: true,
  });
  await expect(planned.locator(".task-card")).toHaveCount(16);
  const header = page.getByRole("heading", { name: "Planned 16", exact: true });
  const before = await header.boundingBox();
  await planned.focus();
  await page.keyboard.press("End");
  await expect
    .poll(() => planned.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  expect(await header.boundingBox()).toEqual(before);
  await page.screenshot({
    path: testInfo.outputPath("milestones-scroll-desktop.png"),
  });
  await planned
    .getByRole("link", { name: "Milestone 15", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Milestone details", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "View tasks", exact: true }).click();
  const selection = new URL(page.url()).searchParams.get("milestone");
  expect(selection).toBeTruthy();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue(selection!);
  await page.reload();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue(selection!);
  await page.goBack();
  await expect(
    page.getByRole("region", { name: "Milestone details", exact: true }),
  ).toBeVisible();
  await page.goForward();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue(selection!);
  await page.goto(`${base}/milestones`);
  await page.setViewportSize({ width: 390, height: 844 });
  const board = page.locator(".board");
  expect(
    await board.evaluate((node) => node.scrollWidth > node.clientWidth),
  ).toBeTruthy();
  await board.evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
  });
  await expect(
    page.getByRole("heading", { name: "Complete 0", exact: true }),
  ).toBeInViewport();
  await page.screenshot({
    path: testInfo.outputPath("milestones-scroll-narrow.png"),
  });
});
