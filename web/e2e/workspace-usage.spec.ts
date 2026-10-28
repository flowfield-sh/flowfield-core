import { settleUsageTheme } from "./usage-inspection";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, state } from "./support";
import { expect } from "@playwright/test";
import { tokens, usageFixture } from "./usage-fixtures";
import { todayPeriod } from "../src/usagePeriod";

function seed() {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/workspace_usage_browser_fixture.py"), state],
      { encoding: "utf8" },
    ),
  );
}

test("workspace timeline and table trace real data through filters, history and task evidence", async ({
  page,
  request,
}, info) => {
  const f = seed();
  const response = await request.get(
    `/api${f.workspace_url.replace("&period=custom", "")}`,
  );
  const data = await response.json();
  expect(data.totals.tokens.total).toBe(4800);
  expect(
    data.buckets.reduce(
      (n: number, b: { tokens: { total: number | null } }) =>
        n + (b.tokens.total ?? 0),
      0,
    ),
  ).toBe(4800);
  await page.goto(f.workspace_url);
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  await expect(usage).toContainText("4,800 tokens reported");
  await expect(usage).toContainText("Partial coverage");
  await expect(
    usage.getByRole("form", { name: "Usage filters" }),
  ).not.toBeVisible();
  await expect(usage.getByText(/Coverage counts executions/)).not.toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Workspace resources" })
      .getByRole("link", { name: "Usage", exact: true }),
  ).toHaveText("Usage");
  await expect(usage.locator(".recharts-bar-rectangle").first()).toBeVisible();
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const colorScheme of ["light", "dark"] as const) {
      await settleUsageTheme(page, colorScheme);
      await usage.scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: "disabled",
        path: info.outputPath(`workspace-${width}-${colorScheme}.png`),
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await usage.getByRole("button", { name: "Table", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(usage.getByRole("table")).toBeVisible();
  await expect(usage.getByRole("table")).toContainText("Not reported");
  await expect(
    usage.getByRole("button", { name: "Table", exact: true }),
  ).toHaveAttribute("data-variant", "default");
  const tableURL = page.url();
  await page.reload();
  await expect(
    usage.getByRole("button", { name: "Table", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const bucket = usage
    .getByRole("row")
    .filter({ hasText: "2,000" })
    .getByRole("link");
  await bucket.focus();
  await page.keyboard.press("Enter");
  await expect(usage).toContainText("2,000 tokens reported");
  const list = usage.getByRole("list", { name: "Counted executions" });
  await list.locator("summary").first().click();
  await expect(list).toContainText(
    "Observation 1 · accepted · 1,000 tokens counted",
  );
  await expect(list).toContainText(
    "Observation 2 · accepted · 1,000 tokens counted",
  );
  await list.getByRole("link", { name: "Open task", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Task usage", exact: true }),
  ).toContainText("3,800 tokens reported");
  await page.getByText("Usage details", { exact: true }).click();
  await page.getByRole("link", { name: "Explore workspace usage" }).click();
  await expect(usage).toContainText("3,800 tokens reported");
  expect(new URL(page.url()).searchParams.get("task_id")).toBe(f.task);
  await page.goBack();
  await page.goBack();
  await page.goBack();
  await expect(page).toHaveURL(tableURL);
  await expect(usage).toContainText("4,800 tokens reported");
  await usage.getByRole("button", { name: "Filters", exact: true }).click();
  await usage.getByLabel("Task", { exact: true }).selectOption("__unassigned");
  await usage.getByRole("button", { name: "Apply filters" }).click();
  await expect(usage).toContainText("300 tokens reported");
  await usage.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(usage.getByLabel("Usage period")).toHaveValue("today");
  expect(new URL(page.url()).searchParams.has("unassigned")).toBe(false);
  await page.goBack();
  await expect(usage).toContainText("300 tokens reported");
  await page.goForward();
  await expect(usage.getByLabel("Usage period")).toHaveValue("today");
});

test("breakdowns include archived tasks and unknown models, partition totals, and restore timezone", async ({
  page,
  request,
}) => {
  const f = seed();
  await page.goto(f.workspace_url);
  const chart = page.locator(".recharts-bar-rectangle").first();
  await chart.click();
  await expect(
    page.getByRole("region", { name: "Workspace usage", exact: true }),
  ).toContainText("2,000 tokens reported");
  await expect(page).toHaveURL(/details=1/);
  await page.goBack();
  await page.goto(f.workspace_url + "&details=1");
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  await expect(usage).toContainText("Archived research (archived)");
  await expect(usage).toContainText("No task assigned");
  await usage
    .getByLabel("Usage breakdown", { exact: true })
    .selectOption("model");
  const table = usage.getByRole("region", { name: "Usage breakdown table" });
  await table.getByRole("link", { name: "Not reported", exact: true }).click();
  await expect(usage).toContainText("2,100 tokens reported");
  await page.goto(f.workspace_url);
  await usage.getByRole("button", { name: "Filters", exact: true }).click();
  await usage.getByLabel("Grouping timezone", { exact: true }).fill("UTC");
  await usage.getByRole("button", { name: "Apply filters" }).click();
  await expect(usage).toContainText("4,800 tokens reported");
  expect(new URL(page.url()).searchParams.get("timezone")).toBe("UTC");
  await page.reload();
  await expect(usage).toContainText("4,800 tokens reported");
  await usage.getByLabel("Group usage").selectOption("day");
  await expect(usage).toContainText("4,800 tokens reported");
  const params = new URL(page.url()).searchParams;
  params.delete("period");
  const data = await (
    await request.get(`/api/workspaces/${f.workspace}/usage?${params}`)
  ).json();
  for (const dimension of [
    "task",
    "role",
    "source",
    "harness",
    "model",
    "purpose",
    "outcome",
  ]) {
    expect(
      data.breakdowns
        .filter((b: { dimension: string }) => b.dimension === dimension)
        .reduce(
          (n: number, b: { tokens: { total: number | null } }) =>
            n + (b.tokens.total ?? 0),
          0,
        ),
    ).toBe(4800);
  }
});

test("loading, empty, error/retry, unavailable, explicit zero, partial/live and bounds stay honest", async ({
  page,
}, info) => {
  const f = seed();
  let failed = true;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const view = usageFixture(f.workspace, f.task);
  view.filters = {
    ...view.filters,
    timezone: "America/Denver",
    group: "hour",
    start: "2026-11-01T06:00:00Z",
    end: "2026-11-02T07:00:00Z",
  };
  view.buckets = [
    {
      start: "2026-11-01T07:00:00Z",
      end: "2026-11-01T08:00:00Z",
      label: "",
      tokens: tokens(0),
      recorded_seconds: 0,
    },
    {
      start: "2026-11-01T08:00:00Z",
      end: "2026-11-01T09:00:00Z",
      label: "",
      tokens: { ...tokens(0), total: null },
      recorded_seconds: null,
    },
  ];
  view.totals.live_intervals = 1;
  await page.route("**/api/workspaces/*/usage?*", async (route) => {
    await gate;
    await route.fulfill(
      failed
        ? { status: 503, json: { detail: "Fixture unavailable" } }
        : { json: view },
    );
  });
  await page.goto(
    f.workspace_url
      .replace("2026-10-27T06", "2026-11-01T06")
      .replace("2026-10-28T06", "2026-11-02T07") + "&view=table",
  );
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  await expect(usage).toContainText("Loading workspace usage");
  await page.screenshot({ path: info.outputPath("loading.png") });
  release();
  await expect(usage).toContainText("Could not refresh workspace usage");
  await expect(usage).not.toContainText("3,800 tokens reported");
  failed = false;
  await usage.getByRole("button", { name: "Retry workspace usage" }).click();
  await expect(usage).toContainText("Live work pending");
  await expect(usage.getByRole("table")).toContainText("GMT-6");
  await expect(usage.getByRole("table")).toContainText("GMT-7");
  await expect(
    usage.getByRole("table").getByRole("cell", { name: "0", exact: true }),
  ).toBeVisible();
  await expect(usage.getByRole("table")).toContainText("Not reported");
  failed = true;
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(usage).toContainText("Showing the last loaded values");
  await page.screenshot({ path: info.outputPath("stale-error.png") });
  failed = false;
  view.limit_reached = true;
  await usage.getByRole("button", { name: "Retry workspace usage" }).click();
  await expect(usage).toContainText("Only a bounded subtotal");
  view.totals.tokens.total = null;
  await page.reload();
  await expect(usage).toContainText("Token usage not reported");
  view.totals.attempts = 0;
  view.buckets = [];
  view.limit_reached = false;
  await page.reload();
  await expect(usage).toContainText("No recorded executions in this period");
  await page.screenshot({ path: info.outputPath("empty.png") });
});

test("Today boundaries follow local calendar days at DST and unusual offsets", () => {
  const fall = todayPeriod(
    "America/Denver",
    Date.parse("2026-11-01T12:00:00Z"),
  );
  expect(fall).toEqual({
    start: "2026-11-01T06:00:00.000Z",
    end: "2026-11-02T07:00:00.000Z",
  });
  const spring = todayPeriod(
    "America/Denver",
    Date.parse("2026-03-08T12:00:00Z"),
  );
  expect(Date.parse(spring.end) - Date.parse(spring.start)).toBe(23 * 3600000);
  expect(
    todayPeriod("Asia/Kathmandu", Date.parse("2026-10-27T12:00:00Z")).start,
  ).toBe("2026-10-26T18:15:00.000Z");
});

test("secondary filters validate exact periods and remain usable on narrow screens", async ({
  page,
}, info) => {
  const f = seed();
  await page.goto(f.workspace_url + "&filters=1");
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await settleUsageTheme(page, "dark");
  await usage
    .getByLabel("Grouping timezone", { exact: true })
    .fill("Invalid/Zone");
  await usage.getByRole("button", { name: "Apply filters" }).click();
  await expect(usage.getByRole("alert")).toContainText(
    "Choose an IANA timezone",
  );
  await usage
    .getByLabel("Grouping timezone", { exact: true })
    .fill("America/Denver");
  await usage
    .getByRole("checkbox", { name: /Only models not reported/ })
    .check();
  await page.screenshot({ path: info.outputPath("filters-narrow-dark.png") });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await usage.getByRole("button", { name: "Apply filters" }).click();
  await expect(usage).toContainText("2,100 tokens reported");
  await usage.getByRole("button", { name: "Table", exact: true }).click();
  const table = usage.getByRole("region", { name: "Reported usage by period" });
  await table.focus();
  await page.keyboard.press("ArrowRight");
  expect(await table.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(
    true,
  );
  await page.screenshot({ path: info.outputPath("table-narrow-dark.png") });
  const url = page.url();
  await page.goto("about:blank");
  await page.goto(url);
  await expect(usage).toContainText("2,100 tokens reported");
  await expect(
    usage.getByRole("button", { name: "Table", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("human date controls preserve UTC instants and intersect secondary filters", async ({
  page,
}) => {
  const f = seed();
  const url = new URL(f.workspace_url, "http://fixture");
  url.searchParams.set("start", "2026-10-27T06:00:00.123Z");
  url.searchParams.set("end", "2026-10-28T06:00:00.456Z");
  url.searchParams.set("filters", "1");
  await page.goto(url.pathname + url.search);
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  const start = usage.getByLabel("Period start (UTC)", { exact: true });
  const end = usage.getByLabel("Period end (UTC, exclusive)", { exact: true });
  await expect(start).toHaveAttribute("type", "datetime-local");
  await expect(start).toHaveValue("2026-10-27T06:00");
  await end.fill("2026-10-27T06:00");
  await usage.getByRole("button", { name: "Apply filters" }).click();
  await expect(usage.getByRole("alert")).toContainText(
    "Choose an end after the start",
  );
  await end.fill("2026-10-28T06:00");
  await usage
    .getByRole("combobox", { name: "Actor role", exact: true })
    .selectOption("worker");
  await usage
    .getByRole("combobox", { name: "Execution source", exact: true })
    .selectOption("run");
  await usage
    .getByRole("combobox", { name: "Harness", exact: true })
    .fill("codex");
  await usage
    .getByRole("combobox", { name: "Coverage", exact: true })
    .selectOption("complete");
  await usage
    .getByRole("combobox", { name: "Outcome", exact: true })
    .fill("accepted");
  await usage
    .getByRole("checkbox", { name: "Only models not reported", exact: true })
    .check();
  await usage.getByRole("button", { name: "Apply filters" }).click();
  await expect(usage).toContainText("1,200 tokens reported");
  const params = new URL(page.url()).searchParams;
  expect(Date.parse(params.get("start")!)).toBe(
    Date.parse("2026-10-27T06:00:00.123Z"),
  );
  expect(Date.parse(params.get("end")!)).toBe(
    Date.parse("2026-10-28T06:00:00.456Z"),
  );
  await page.reload();
  await expect(usage).toContainText("1,200 tokens reported");
  await usage.getByRole("button", { name: /^Filters/ }).click();
  await expect(
    usage.getByRole("combobox", { name: "Harness", exact: true }),
  ).toHaveValue("codex");
  await expect(
    usage.getByRole("combobox", { name: "Execution source", exact: true }),
  ).toHaveValue("run");
});
