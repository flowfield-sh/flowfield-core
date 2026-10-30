import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, state } from "./support";
import { expect } from "@playwright/test";
import {
  metricsQuery,
  presetRange,
  localDateTime,
  parseLocalDateTime,
} from "../src/usagePeriod";
import { dialogViews } from "./dialog-evidence";

function seed() {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/workspace_usage_browser_fixture.py"), state],
      { encoding: "utf8" },
    ),
  );
}
test.use({ timezoneId: "America/Denver" });

test("Metrics shows both charts and sortable task costs with shared local datetime controls", async ({
  page,
}, info) => {
  const f = seed();
  await page.goto(f.workspace_url);
  const metrics = page.getByRole("region", {
    name: "Workspace metrics",
    exact: true,
  });
  await expect(
    metrics.getByRole("heading", { name: "Metrics", exact: true }),
  ).toHaveCount(0);
  await expect(
    metrics.getByRole("combobox", { name: "Chart metric" }),
  ).toHaveCount(0);
  await expect(metrics.locator(".usage-totals dd").first()).toHaveText("4,800");
  const tokens = metrics.getByRole("region", { name: "Token usage over time" });
  const cost = metrics.getByRole("region", { name: "Cost over time" });
  await expect(
    cost.locator(".recharts-bar-rectangle path").first(),
  ).toBeVisible();
  await expect(
    metrics.getByRole("button", { name: "Cost breakdown: -", exact: true }),
  ).toBeVisible();
  const duration = metrics.getByRole("region", { name: "Duration over time" });
  await expect(
    tokens.locator(".recharts-bar-rectangle path").first(),
  ).toBeVisible();
  await expect(duration.locator(".recharts-line-dot").first()).toBeVisible();
  await expect(metrics.getByLabel("From", { exact: true })).toHaveValue(
    "2026-10-27T00:00",
  );
  await expect(metrics.getByLabel("To", { exact: true })).toHaveValue(
    "2026-10-28T00:00",
  );
  const table = metrics.getByRole("table");
  // Wheel navigation must reach the lower chart/table without automation
  // scrolling a hidden-overflow ancestor on our behalf.
  const from = metrics.getByLabel("From", { exact: true });
  const bounds = (await from.boundingBox())!;
  await page.mouse.move(bounds.x + 40, bounds.y + 15);
  await page.mouse.wheel(0, 2400);
  await expect(table).toBeInViewport();
  await expect(from).not.toBeInViewport();
  await page.mouse.wheel(0, -2400);
  await expect(from).toBeInViewport();
  await expect(
    table.getByRole("columnheader", { name: "Tokens" }),
  ).toHaveAttribute("aria-sort", "descending");
  await expect(table.locator("tbody tr").first()).toContainText(
    "Trace recorded task usage",
  );
  await expect(table).not.toContainText("Coordinator");
  await table.getByRole("button", { name: "Cost", exact: true }).click();
  await expect(
    table.getByRole("columnheader", { name: "Cost", exact: true }),
  ).toHaveAttribute("aria-sort", "descending");
  await table.getByRole("button", { name: "Duration" }).click();
  await expect(
    table.getByRole("columnheader", { name: "Duration" }),
  ).toHaveAttribute("aria-sort", "descending");
  await table.getByRole("button", { name: "Duration" }).click();
  await expect(
    table.getByRole("columnheader", { name: "Duration" }),
  ).toHaveAttribute("aria-sort", "ascending");
  await expect(table.locator("tbody tr").last()).toContainText(
    "Usage not reported",
  );
  await metrics.getByLabel("Chart grouping").selectOption("harness");
  await expect(tokens.getByLabel("Chart legend")).toContainText("Claude Code");
  await expect(duration.getByLabel("Chart legend")).toContainText(
    "Claude Code",
  );
  await dialogViews(page, info, "metrics-both-charts");
  await metrics.getByLabel("Metrics activity").selectOption("coordinator");
  await expect(metrics.locator(".usage-totals dd").first()).toHaveText("300");
  await expect(tokens.getByLabel("Chart legend")).not.toContainText("Worker");
  await expect(metrics).toContainText("No task activity in this period.");
  await metrics.getByLabel("Metrics activity").selectOption(f.task);
  await expect(metrics.locator(".usage-totals dd").first()).toHaveText("3,800");
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await table.getByRole("link").click();
  await expect(page).toHaveURL(/\/tasks\/T-1$/);
  await expect(
    page.getByRole("region", { name: "Task metrics", exact: true }),
  ).toBeVisible();
});

test("preset-only Metrics links compute their range on load and preserve exact history", async ({
  page,
}) => {
  const f = seed();
  await page.clock.setFixedTime(new Date("2026-10-29T18:00:00Z"));
  await page.goto(`/workspaces/${f.workspace}/metrics?period=month`);
  const metrics = page.getByRole("region", {
    name: "Workspace metrics",
    exact: true,
  });
  await expect(metrics.locator(".usage-totals dd").first()).toHaveText("4,800");
  await expect(metrics.getByLabel("From", { exact: true })).toHaveValue(
    "2026-09-30T00:00",
  );
  await expect(metrics.getByLabel("To", { exact: true })).toHaveValue(
    "2026-10-29T12:00",
  );
  await expect(metrics.getByLabel("Metrics period")).toHaveValue("month");
  const month = page.url();
  await page.reload();
  await expect(page).toHaveURL(month);
  await metrics.getByLabel("Metrics period").selectOption("quarter");
  await expect(metrics.getByLabel("From", { exact: true })).toHaveValue(
    "2026-07-29T12:00",
  );
  await metrics.getByLabel("Metrics period").selectOption("year");
  await expect(metrics.getByLabel("From", { exact: true })).toHaveValue(
    "2026-01-01T00:00",
  );
  await page.goBack();
  await expect(metrics.getByLabel("Metrics period")).toHaveValue("quarter");
  await page.goBack();
  await expect(page).toHaveURL(month);
});

test("both charts select the visible datetime range without zoom state or tooltip motion", async ({
  page,
}) => {
  const f = seed();
  await page.goto(
    `/workspaces/${f.workspace}/metrics?start=2026-10-25T00:00:00Z&end=2026-10-29T00:00:00Z`,
  );
  const metrics = page.getByRole("region", {
    name: "Workspace metrics",
    exact: true,
  });
  const tokens = metrics.getByRole("region", { name: "Token usage over time" });
  const duration = metrics.getByRole("region", { name: "Duration over time" });
  await metrics.getByLabel("Chart grouping").selectOption("model");
  const bar = tokens.locator(".recharts-bar-rectangle path").first();
  await bar.hover();
  await expect(tokens.locator(".usage-chart-tooltip")).toBeVisible();
  expect(
    await tokens
      .locator(".recharts-tooltip-wrapper")
      .evaluate((e) => getComputedStyle(e).transitionDuration),
  ).toBe("0s");
  await bar.click();
  await expect(page).toHaveURL(/group=hour/);
  await expect(metrics.getByLabel("Metrics period")).toHaveValue("custom");
  await expect(metrics.getByLabel("From", { exact: true })).toBeVisible();
  await expect(metrics.getByRole("button", { name: "Zoom out" })).toHaveCount(
    0,
  );
  expect(new URL(page.url()).searchParams.has("zoom")).toBe(false);
  await expect(duration.locator(".recharts-line-dot").first()).toBeVisible();
  const svg = duration.locator("svg.recharts-surface");
  await svg.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/group=minute/);
  const selected = page.url();
  await page.reload();
  await expect(page).toHaveURL(selected);
  await expect(metrics.getByLabel("Chart grouping")).toHaveValue("model");
  await metrics.getByLabel("From", { exact: true }).fill("2026-10-27T04:05");
  await metrics.getByLabel("To", { exact: true }).fill("2026-10-27T04:25");
  await metrics.getByRole("button", { name: "Apply", exact: true }).click();
  expect(new URL(page.url()).searchParams.get("start")).toBe(
    "2026-10-27T10:05:00.000Z",
  );
  expect(new URL(page.url()).searchParams.get("end")).toBe(
    "2026-10-27T10:25:00.000Z",
  );
  await expect(tokens.locator("svg").first()).toBeVisible();
  await expect(duration.locator("svg").first()).toBeVisible();
});

test("Metrics has pending, retry, empty and invalid-range recovery", async ({
  page,
}, info) => {
  const f = seed();
  let release!: () => void;
  const pending = new Promise<void>((resolve) => (release = resolve));
  let fail = true;
  await page.route("**/api/workspaces/*/usage?*", async (route) => {
    await pending;
    return fail
      ? route.fulfill({ status: 503, json: { detail: "Read failed" } })
      : route.continue();
  });
  await page.goto(f.workspace_url);
  const metrics = page.getByRole("region", {
    name: "Workspace metrics",
    exact: true,
  });
  await expect(metrics).toContainText("Loading metrics");
  await dialogViews(page, info, "metrics-pending");
  release();
  await expect(metrics).toContainText("Could not refresh metrics");
  fail = false;
  await metrics.getByRole("button", { name: "Retry metrics" }).click();
  await expect(metrics.locator(".usage-totals dd").first()).toHaveText("4,800");
  await metrics.getByLabel("From", { exact: true }).fill("2020-01-01T00:00");
  await metrics.getByLabel("To", { exact: true }).fill("2020-01-02T00:00");
  await metrics.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(metrics).toContainText("No metrics in this period.");
  await dialogViews(page, info, "metrics-empty");
  await page.goto(
    `/workspaces/${f.workspace}/metrics?start=invalid&end=2026-10-29T00:00:00Z&task_id=${f.task}`,
  );
  await metrics.getByRole("button", { name: "Reset dates" }).click();
  await expect(metrics.getByLabel("Metrics activity")).toHaveValue(f.task);
  await expect(metrics.getByLabel("From", { exact: true })).not.toHaveValue("");
});

test("local presets clamp calendar months, handle DST and preserve a repeated-hour selection", () => {
  const original = process.env.TZ;
  process.env.TZ = "America/Denver";
  try {
    expect(
      presetRange("quarter", Date.parse("2024-05-31T18:00:00Z")).start,
    ).toBe("2024-02-29T19:00:00.000Z");
    expect(presetRange("year", Date.parse("2026-10-29T18:00:00Z")).start).toBe(
      "2026-01-01T07:00:00.000Z",
    );
    expect(presetRange("today", Date.parse("2026-03-08T18:00:00Z")).start).toBe(
      "2026-03-08T07:00:00.000Z",
    );
    expect(() => parseLocalDateTime("2026-03-08T02:30")).toThrow();
    const repeated = "2026-11-01T08:30:00.000Z";
    expect(parseLocalDateTime(localDateTime(repeated), repeated)).toBe(
      repeated,
    );
    const query = metricsQuery(
      "period=month",
      Date.parse("2026-10-29T18:00:00Z"),
    );
    expect(query.get("start")).toBe("2026-09-30T06:00:00.000Z");
    expect(query.get("period")).toBe("month");
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
});
