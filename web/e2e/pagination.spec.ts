import { join } from "node:path";
import { expect, type Route } from "@playwright/test";
import { pageWindow, nextPage, type Page } from "../src/pagination";
import { test, fixtureProject, existingDirectory, state } from "./support";

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
}) => {
  const project = "pagination-recovery";
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, project)),
      task_prefix: "PGR",
    },
  });
  const task = await (
    await request.post(`/api/projects/${project}/tasks`, {
      data: { title: "Review recovery" },
    })
  ).json();
  await page.route("**/api/events", (route) => route.abort());
  await page.route(
    `**/api/projects/${project}/tasks/${task.id}/review`,
    (route) =>
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
  await page.route(
    `**/api/projects/${project}/reviews/review/attempts*`,
    (route) => {
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
    },
  );
  await page.goto(`/projects/${project}/tasks/${task.key}`);
  await page
    .getByText("Review rounds and diagnostics", { exact: true })
    .click();
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
});
