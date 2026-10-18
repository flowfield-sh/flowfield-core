import { projectWorkspace } from "./support";
import { join } from "node:path";
import { expect, type Route } from "@playwright/test";
import { pageWindow, nextPage, type Page } from "../src/pagination";
import {
  test,
  fixtureProject,
  fixtureProgress,
  existingDirectory,
  state,
} from "./support";

test("history refresh stops repeated and empty continuing cursors", async () => {
  for (const pages of [
    [
      { items: [1], next_before: 9 },
      { items: [], next_before: 9 },
    ],
    [
      { items: [1], next_before: 9 },
      { items: [], next_before: 8 },
    ],
    [
      { items: [1], next_before: 9 },
      { items: [2], next_before: 8 },
      { items: [3], next_before: 9 },
    ],
  ]) {
    const pending = [...pages];
    await expect(
      pageWindow("history", 20, async () => {
        const page = pending.shift();
        if (!page) throw new Error("Read beyond the broken cursor");
        return page;
      }),
    ).rejects.toThrow("Could not advance through this list");
    expect(pending).toHaveLength(0);
  }
});

test("history refresh handles shrinking collections and zero offset cursors", async () => {
  const urls: string[] = [];
  const pages: Page[] = [
    { items: [1], next_offset: 0 },
    { items: [2], next_offset: 2 },
    { items: [], next_offset: null },
  ];
  const value = await pageWindow("history?limit=1", 4, async (url) => {
    urls.push(url);
    return pages.shift()!;
  });
  expect(value.items).toEqual([1, 2]);
  expect(urls).toEqual([
    "history?limit=1",
    "history?limit=1&offset=0",
    "history?limit=1&offset=2",
  ]);
  await expect(
    nextPage("history", { items: [1], next_before: 9 }, async () => ({
      items: [1],
      next_before: 9,
    })),
  ).rejects.toThrow("Could not advance");
});

test("service recovery cancels pending review pagination with an unchanged review revision", async ({
  page,
  request,
}, testInfo) => {
  const project = "pagination-recovery";
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, project)),
    },
  });
  const task = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
      {
        data: { title: "Review recovery" },
      },
    )
  ).json();
  await fixtureProgress(request, {
    project_id: project,
    task_id: task.id,
    progress: { status: "done", expected_revision: task.revision },
  });
  await page.route("**/api/events", (route) => route.abort());
  await page.route(
    `**/api/workspaces/*/tasks/${task.id}/thread/review*`,
    (route) =>
      route.fulfill({
        json: {
          id: "review:review",
          kind: "review",
          source_id: "review",
          revision: 1,
          created_at: "2026-10-16T12:00:00Z",
          author: "reviewer",
          actor: { kind: "reviewer", label: "Reviewer" },
          state: { label: "Review failed", tone: "attention" },
          title: "Reviewing results",
          body: "",
          status: "failed",
        },
      }),
  );
  await page.route("**/api/workspaces/*/reviews/review", (route) =>
    route.fulfill({
      json: {
        id: "review",
        revision: 1,
        status: "failed",
        problem: "Retained review failure",
      },
    }),
  );
  await page.route(`**/api/workspaces/*/tasks/${task.id}/review`, (route) =>
    route.fulfill({
      json: {
        id: "review",
        revision: 1,
        status: "failed",
        problem: "Retained review failure",
      },
    }),
  );
  const round = (id: string, role = "reviewer") => ({
    id,
    role,
    created_at: "2026-10-16T12:00:00Z",
    status: "completed",
    cleanup_confirmed: true,
  });
  let recovering = false;
  let pending: Route | undefined;
  let cancelled = false;
  page.on("requestfailed", (request) => {
    if (request.url().endsWith("/attempts?before=10")) cancelled = true;
  });
  await page.route(`**/api/workspaces/*/reviews/review/attempts*`, (route) => {
    if (route.request().url().includes("?")) {
      pending = route;
      return;
    }
    return route.fulfill({
      json: {
        items: [round(recovering ? "fresh" : "original")],
        next_before: recovering ? null : 10,
      },
    });
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(request, project)}/tasks/${task.key}/conversation/review:review`,
  );
  await expect(page.locator(".entity-pane-footer")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("review-recovery-desktop.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Earlier rounds" }).click();
  await expect.poll(() => !!pending).toBe(true);
  recovering = true;
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  try {
    await expect.poll(() => cancelled).toBe(true);
  } finally {
    await pending!.fulfill({
      json: { items: [round("stale", "implementer")], next_before: null },
    });
  }
  await expect(
    page.locator("summary").filter({ hasText: /^Reviewer ·/ }),
  ).toHaveCount(1);
  await expect(
    page.locator("summary").filter({ hasText: /^Implementer ·/ }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Earlier rounds" }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.screenshot({
    path: testInfo.outputPath("review-recovery-narrow-dark.png"),
    animations: "disabled",
  });
});
