import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, existingDirectory, state, fixtureStages } from "./support";

test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } });

test("board columns scroll independently with fixed headings and reachable cards", async ({
  page,
  request,
}, testInfo) => {
  const project = "column-scroll";
  expect(
    (
      await request.post("/api/projects/initialize", {
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
  // Report occupancy without starting a model or changing the paused queue.
  await page.route(`**/api/projects/${project}/workers/occupancy`, (route) =>
    route.fulfill({ json: { active: 1, uncertain: 0 } }),
  );
  await page.route(`**/api/projects/${project}/workers`, async (route) => {
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

test("Needs you shares board scrolling and wraps long cards", async ({
  page,
  request,
}, testInfo) => {
  const project = "attention-scroll";
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "ASC",
        },
      })
    ).ok(),
  ).toBe(true);
  await page.route(`**/api/projects/${project}/view/attention?*`, (route) => {
    const query = new URL(route.request().url()).searchParams;
    const column = query.get("column")!;
    const offset = Number(query.get("offset") ?? 0);
    return route.fulfill({
      json: {
        items: Array.from({ length: offset ? 1 : 14 }, (_, i) => ({
          id: `${column}-${offset + i}`,
          kind:
            column === "waiting" && offset + i === 0
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
  });
  await page.route(
    "**/api/projects/" + project + "/permissions/waiting-0?*",
    (route) =>
      route.fulfill({
        json: {
          id: "waiting-0",
          project_id: project,
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
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.goto(`/projects/${project}/inbox`);
  const board = page.locator(".attention-board");
  const action = page.getByRole("region", {
    name: "Needs your action items",
    exact: true,
  });
  const waiting = page.getByRole("region", {
    name: "Waiting items",
    exact: true,
  });
  await expect(action.getByRole("link")).toHaveCount(14);
  await page.addStyleTag({
    content: `* { scrollbar-width: auto; } ::-webkit-scrollbar { width: 14px; height: 14px; }`,
  });
  const positions = () =>
    board
      .locator(".column > h2")
      .evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().top));
  const initial = await positions();
  await expect(
    waiting.getByRole("button", { name: "Allow_".repeat(80), exact: true }),
  ).toBeVisible();
  await action.focus();
  await page.keyboard.press("End");
  await expect
    .poll(() => action.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  expect(await waiting.evaluate((node) => node.scrollTop)).toBe(0);
  expect(await positions()).toEqual(initial);
  await action.getByRole("button", { name: "Load more" }).click();
  await expect(action.getByRole("link")).toHaveCount(15);
  await action.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await expect(action.getByRole("link").last()).toBeInViewport();
  await waiting.hover();
  await page.mouse.wheel(0, 500);
  await expect
    .poll(() => waiting.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  expect(await positions()).toEqual(initial);
  for (const width of [1800, 390]) {
    await page.setViewportSize({ width, height: 800 });
    expect(
      await board
        .locator(".column-cards")
        .evaluateAll((nodes) =>
          nodes.every((n) => n.scrollWidth <= n.clientWidth),
        ),
    ).toBe(true);
    expect(
      await board
        .locator("li")
        .evaluateAll((nodes) =>
          nodes.every((n) => n.scrollWidth <= n.clientWidth),
        ),
    ).toBe(true);
    for (const selector of [
      ".attention-board",
      ".workspace-work-content",
      "html",
    ]) {
      expect(
        await page
          .locator(selector)
          .evaluate((node) => node.scrollHeight <= node.clientHeight),
      ).toBe(true);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (width === 390) {
      expect(
        await board.evaluate((node) => node.scrollWidth > node.clientWidth),
      ).toBe(true);
      await board.evaluate((node) => {
        node.scrollLeft = node.scrollWidth;
      });
      await expect(
        board.getByRole("heading", { name: "History" }),
      ).toBeInViewport();
    }
    await page.screenshot({
      path: testInfo.outputPath(`attention-columns-${width}.png`),
    });
  }
});
