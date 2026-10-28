import { settleUsageTheme } from "./usage-inspection";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { test } from "./support";
import {
  usageFixture,
  usageEvidenceFixture,
  unknownTokens,
  tokens,
} from "./usage-fixtures";
import type { UsageView } from "../src/usage";

async function setup(request: APIRequestContext) {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Usage inspection",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  const task = await (
    await request.post(`/api/workspaces/${workspace.id}/tasks`, {
      data: {
        title: "Trace recorded work",
        body: "A compact task summary with exact execution evidence.",
      },
    })
  ).json();
  return {
    workspace: workspace.id as string,
    task,
    url: `/workspaces/${workspace.id}/tasks/${task.key}`,
  };
}
async function stub(page: Page, view: UsageView) {
  await page.route("**/tasks/*/usage?*", (route) =>
    route.fulfill({ json: view }),
  );
  await page.route("**/usage/*/*?*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    return route.fulfill({
      json: usageEvidenceFixture(
        view.items.find((item) => item.source_id === id)!,
      ),
    });
  });
}

test("compact totals disclose exact attempts, role/source partitions and baseline evidence", async ({
  page,
  request,
}, info) => {
  const f = await setup(request);
  const view = usageFixture(f.workspace, f.task.id);
  await stub(page, view);
  const requests: string[] = [];
  page.on("request", (r) => {
    if (/\/usage\/(run|review_attempt)\//.test(r.url())) requests.push(r.url());
  });
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("3,800 tokens reported");
  await expect(usage).toContainText("48m recorded execution time");
  await expect(
    usage.getByText("Partial coverage", { exact: true }),
  ).toBeVisible();
  await expect(usage.getByText(/7 execution attempts/)).not.toBeVisible();
  expect(requests).toHaveLength(0);
  await expect(usage.locator(":scope > h4")).toHaveCSS("font-size", "16px");
  await expect(usage.locator("summary").first()).toHaveCSS("font-size", "14px");
  await expect(usage.getByText("Partial coverage", { exact: true })).toHaveCSS(
    "font-size",
    "12px",
  );
  await expect(usage.locator("time")).toHaveCount(0);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const colorScheme of ["light", "dark"] as const) {
      await settleUsageTheme(page, colorScheme);
      await usage.scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: "disabled",
        path: info.outputPath(`summary-${width}-${colorScheme}.png`),
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  }
  const details = usage
    .locator("summary")
    .filter({ hasText: /^Usage details$/ });
  await expect(details).toHaveCSS("display", "flex");
  await details.focus();
  await page.keyboard.press("Enter");
  await expect(usage.getByText(/7 execution attempts/)).toBeVisible();
  await expect(
    usage.getByText(/Parallel executions can exceed/),
  ).not.toBeVisible();
  await usage
    .locator("summary")
    .filter({ hasText: /^Accounting and coverage$/ })
    .click();
  await expect(usage).toContainText(
    "1 related Coordinator attempts are excluded",
  );
  await expect(usage).toContainText(
    "Parallel executions can exceed elapsed task time",
  );
  await usage
    .locator("summary")
    .filter({ hasText: "Role and source breakdowns" })
    .click();
  await expect(usage).toContainText("worker: 3,500 tokens · 6 attempts");
  await expect(usage).toContainText("review attempt: 450 tokens · 2 attempts");
  const first = usage
    .getByRole("list", { name: "Counted executions" })
    .locator("li")
    .first();
  await first.locator("summary").first().focus();
  await page.keyboard.press("Space");
  await expect(first).toContainText("Source: run · execution-1");
  await expect(
    first.getByRole("link", { name: "Open exact attempt" }),
  ).toHaveAttribute(
    "href",
    `/workspaces/${f.workspace}/requests/attempt/execution-1`,
  );
  await first.locator("summary").filter({ hasText: "Observation 1" }).click();
  await expect(first).toContainText("Baseline: resume");
  await expect(
    first.getByRole("row", { name: "total 3,000 1,000 2,000", exact: true }),
  ).toBeVisible();
  await expect(first).toContainText("query pipeline including native children");
  await expect(first).toContainText("Source time: Not recorded");
  expect(requests).toHaveLength(1);
  const scroll = first.getByRole("region", {
    name: "Execution numeric evidence",
    exact: true,
  });
  await scroll.focus();
  await page.keyboard.press("End");
  await expect(
    first.getByText(/totals are never spread proportionally/),
  ).toBeInViewport();
  await page.keyboard.press("Home");
  await expect(
    first.getByText("Source: run · execution-1", { exact: true }),
  ).toBeInViewport();
  await expect(scroll).toHaveCSS("scrollbar-gutter", "auto");
  await scroll.hover();
  await page.mouse.wheel(0, 480);
  await expect
    .poll(() => scroll.evaluate((el) => el.scrollTop))
    .toBeGreaterThan(0);
  await page.mouse.wheel(0, -2000);
  await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBe(0);
  const time = scroll.locator("time").first();
  await expect(time).toHaveAttribute("datetime", view.items[0].started_at!);
  await expect(time).not.toHaveText(view.items[0].started_at!);
  await time.focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await expect(time).toHaveAttribute("aria-label", /2026.*\d+:\d+:\d+/);
  await page.keyboard.press("Escape");
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const colorScheme of ["light", "dark"] as const) {
      await settleUsageTheme(page, colorScheme);
      await first.getByRole("table").scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: "disabled",
        path: info.outputPath(`evidence-${width}-${colorScheme}.png`),
      });
    }
  }
  const numeric = first.getByRole("region", {
    name: "Numeric provenance",
    exact: true,
  });
  await numeric.focus();
  for (let i = 0; i < 8; i++) await page.keyboard.press("ArrowRight");
  await expect
    .poll(() => numeric.evaluate((node) => node.scrollLeft))
    .toBeGreaterThan(0);
});

test("loading, failed read and retry preserve unknown values and measured zero", async ({
  page,
  request,
}, info) => {
  const f = await setup(request);
  let mode = "loading";
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const view = usageFixture(f.workspace, f.task.id);
  view.totals = {
    ...view.totals,
    tokens: unknownTokens,
    recorded_seconds: null,
    coverage: "unavailable",
  };
  await page.route("**/tasks/*/usage?*", async (route) => {
    if (mode === "loading") await pending;
    if (mode === "failed")
      await route.fulfill({
        status: 503,
        json: { detail: "Fixture read failed" },
      });
    else await route.fulfill({ json: view });
  });
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("Loading task usage…");
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("loading.png"),
  });
  await expect(usage).not.toContainText("0 tokens");
  mode = "failed";
  release();
  await expect(usage).toContainText("Could not refresh task usage");
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("failed.png"),
  });
  mode = "ready";
  await usage.getByRole("button", { name: "Retry task usage" }).click();
  await expect(usage).toContainText("Token usage not reported");
  await expect(usage).toContainText("Time unavailable");
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("unavailable.png"),
  });
  view.totals.tokens = tokens(0);
  view.totals.recorded_seconds = 0;
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(usage).toContainText("0 tokens reported");
  await expect(usage).toContainText("0s recorded execution time");
  mode = "failed";
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(usage).toContainText("Showing the last loaded values");
  await expect(usage).toContainText("0 tokens reported");
});

test("real empty task does not claim zero measured consumption", async ({
  page,
  request,
}, info) => {
  const f = await setup(request);
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("No recorded executions yet");
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("empty.png"),
  });
  await expect(usage).not.toContainText("0 tokens");
});

test("pagination keeps aggregate totals and refreshed windows with live and long evidence", async ({
  page,
  request,
}, info) => {
  const f = await setup(request);
  const view = usageFixture(f.workspace, f.task.id);
  view.totals.live_intervals = 1;
  view.items[0].timing = "live";
  view.items[0].ended_at = null;
  view.items[0].observed_models = ["long-reported-model-".repeat(25)];
  const queries: string[] = [];
  await page.route("**/tasks/*/usage?*", (route) => {
    const after = new URL(route.request().url()).searchParams.get("after");
    queries.push(after ?? "first");
    return route.fulfill({
      json: {
        ...view,
        items: after ? view.items.slice(2) : view.items.slice(0, 2),
        next_after: after ? null : 2,
      },
    });
  });
  await page.route("**/usage/run/*?*", (route) => {
    const after = new URL(route.request().url()).searchParams.get("after");
    const evidence = usageEvidenceFixture(view.items[0]);
    if (after)
      evidence.items[0] = {
        ...evidence.items[0],
        number: 2,
        delta: tokens(0),
        disposition: "reordered",
      };
    return route.fulfill({
      json: { ...evidence, next_after: after ? null : 1 },
    });
  });
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("Live work pending");
  await usage
    .locator("summary")
    .filter({ hasText: /^Usage details$/ })
    .click();
  await usage.getByRole("button", { name: "More executions" }).click();
  await expect(
    usage
      .getByRole("list", { name: "Counted executions" })
      .locator(":scope > li"),
  ).toHaveCount(7);
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(
    usage
      .getByRole("list", { name: "Counted executions" })
      .locator(":scope > li"),
  ).toHaveCount(7);
  expect(queries).toContain("2");
  await expect(usage).toContainText("3,800 tokens reported");
  const first = usage
    .getByRole("list", { name: "Counted executions" })
    .locator("li")
    .first();
  await first.locator("summary").first().click();
  await first.getByRole("button", { name: "More observations" }).click();
  await expect(first).toContainText(
    "Observation 2 · reordered · 0 tokens counted",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await settleUsageTheme(page, "dark");
  await first.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("live-long-narrow-dark.png"),
  });
});

test("bounded reads cannot appear complete", async ({ page, request }) => {
  const f = await setup(request);
  const view = usageFixture(f.workspace, f.task.id);
  view.limit_reached = true;
  view.totals.coverage = "complete";
  await stub(page, view);
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("read limit reached");
  await usage
    .locator("summary")
    .filter({ hasText: /^Usage details$/ })
    .click();
  await usage
    .locator("summary")
    .filter({ hasText: /^Accounting and coverage$/ })
    .click();
  await expect(usage.getByText(/Narrow the period or filters/)).toBeVisible();
});

test("durable production reads trace counted deltas and preserve the exact result journey", async ({
  page,
  request,
}, info) => {
  const { execFileSync } = await import("node:child_process");
  const { resolve } = await import("node:path");
  const { state } = await import("./support");
  const f = JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/task_usage_browser_fixture.py"), state],
      { encoding: "utf8" },
    ),
  );
  const response = await request.get(
    `/api/workspaces/${f.workspace}/tasks/${f.task}/usage`,
  );
  expect(response.ok()).toBe(true);
  const view = await response.json();
  expect(view.totals.tokens.total).toBe(3800);
  expect(view.totals.attempts).toBe(3);
  expect(view.totals.recorded_seconds).toBe(3600);
  expect(
    view.items.map((item: { harness: string }) => item.harness).sort(),
  ).toEqual(["claude-code", "codex", "pi"]);
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage.getByText(/3,800 tokens reported/)).toBeVisible();
  await expect(
    usage.getByText("Partial coverage", { exact: true }).first(),
  ).toBeVisible();
  await usage
    .locator("summary")
    .filter({ hasText: /^Usage details$/ })
    .click();
  const rows = usage
    .getByRole("list", { name: "Counted executions" })
    .locator(":scope > li");
  let counted = 0;
  for (let index = 0; index < 3; index++) {
    const row = rows.nth(index);
    await row.locator("summary").first().click();
    const evidenceUrl = await row
      .getByRole("link", { name: "Exact numeric evidence (JSON)" })
      .getAttribute("href");
    const evidence = await (await request.get(evidenceUrl!)).json();
    expect(f.runs).toContain(evidence.execution.source_id);
    const subtotal = evidence.items.reduce(
      (sum: number, item: { delta: { total: number } }) =>
        sum + item.delta.total,
      0,
    );
    expect(subtotal).toBe(evidence.execution.tokens.total);
    counted += subtotal;
    await row
      .locator("summary")
      .filter({ hasText: `Observation ${evidence.items[0].number} ·` })
      .click();
    if (evidence.execution.harness === "codex") {
      await expect(row).toContainText("Native scope ID: synthetic-codex");
      await expect(row).toContainText(
        "Uncertain counters: cache read, cache write, reasoning",
      );
      await expect(row).toContainText("Reported model: Unknown");
    }
    if (evidence.execution.harness === "pi") {
      await expect(row).toContainText(
        "Scope limitations: native provider coverage unknown",
      );
      await expect(row).toContainText("native session all entries");
    }
    await row.locator("summary").first().click();
  }
  expect(counted).toBe(view.totals.tokens.total);
  await usage
    .locator("summary")
    .filter({ hasText: /^Usage details$/ })
    .click();
  // Current task notices must remain above Definition, with Usage below it.
  const notice = page.locator(".task-notices");
  const definition = page.getByLabel("Current task definition");
  expect((await notice.boundingBox())!.y).toBeLessThan(
    (await definition.boundingBox())!.y,
  );
  expect((await definition.boundingBox())!.y).toBeLessThan(
    (await usage.boundingBox())!.y,
  );
  await expect(
    notice.getByRole("link", { name: "View result", exact: true }),
  ).toBeVisible();
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const colorScheme of ["light", "dark"] as const) {
      await settleUsageTheme(page, colorScheme);
      await page
        .getByRole("heading", {
          name: "Trace recorded task usage",
          exact: true,
        })
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: "disabled",
        path: info.outputPath(`production-${width}-${colorScheme}.png`),
      });
      await page
        .getByText("Moved to Done", { exact: true })
        .last()
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: "disabled",
        path: info.outputPath(`production-feed-${width}-${colorScheme}.png`),
      });
    }
  }
  const resultLink = page
    .locator(`a[href='/workspaces/${f.workspace}/results/${f.result}']`)
    .first();
  await resultLink.click();
  await expect(page).toHaveURL(new RegExp(`/results/${f.result}$`));
  await expect(
    page.getByText("Report complete", { exact: true }).first(),
  ).toBeVisible();
});

test("execution failures retry locally, and missing evidence stays unavailable", async ({
  page,
  request,
}) => {
  const f = await setup(request);
  const view = usageFixture(f.workspace, f.task.id);
  await stub(page, view);
  let failed = true;
  await page.route("**/usage/run/execution-7?*", (route) =>
    failed
      ? route.fulfill({ status: 503, json: { detail: "Evidence failed" } })
      : route.fulfill({ json: usageEvidenceFixture(view.items[6]) }),
  );
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await usage
    .locator("summary")
    .filter({ hasText: /^Usage details$/ })
    .click();
  const last = usage
    .getByRole("list", { name: "Counted executions" })
    .locator("li")
    .last();
  await last.locator("summary").first().click();
  await expect(last).toContainText("Could not refresh execution evidence");
  failed = false;
  await last.getByRole("button", { name: "Retry execution evidence" }).click();
  await expect(last).toContainText("No consumption observations retained");
  await expect(last).toContainText("Timing: unknown end");
  await expect(last).not.toContainText("0 tokens");
  await expect(usage).toContainText("3,800 tokens reported");
});
