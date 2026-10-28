import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, state } from "./support";
import { expect } from "@playwright/test";
import { todayPeriod } from "../src/usagePeriod";
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

test("usage fills its pane, retains Coordinator and filters persisted counters by task and date", async ({
  page,
  request,
}, info) => {
  const f = seed();
  await page.goto(f.workspace_url);
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  await expect(usage.locator("dd").first()).toHaveText("4,800");
  await expect(usage.getByLabel("coordinator usage")).toContainText("300");
  await expect(usage.getByRole("table")).toHaveCount(0);
  const chart = usage.getByRole("region", { name: "Token usage over time" });
  await expect(chart.locator("svg").first()).toBeVisible();
  const panelBounds = (await usage.boundingBox())!,
    chartBounds = (await chart.boundingBox())!;
  expect(chartBounds.width).toBeGreaterThan(panelBounds.width - 50);
  await expect(
    page.getByRole("tabpanel", { name: "Coordinator", exact: true }),
  ).toBeVisible();
  await dialogViews(page, info, "workspace-usage");
  await usage.getByLabel("Usage task").selectOption(f.task);
  await expect(usage.getByLabel("coordinator usage")).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`task_id=${f.task}`));
  const query = new URL(page.url()).searchParams;
  query.delete("period");
  const expected = await (
    await request.get(`/api/workspaces/${f.workspace}/usage?${query}`)
  ).json();
  await expect(usage.locator("dd").first()).toHaveText(
    expected.totals.tokens.total.toLocaleString(),
  );
  await page.reload();
  await expect(usage.getByLabel("Usage task")).toHaveValue(f.task);
  await usage.getByLabel("Usage period").selectOption("week");
  await expect(page).toHaveURL(/period=week/);
  await page.goBack();
  await expect(usage.getByLabel("Usage period")).toHaveValue("custom");
  await usage.getByLabel("From", { exact: true }).fill("2026-10-26");
  await usage.getByLabel("Through", { exact: true }).fill("2026-10-28");
  await usage.getByRole("button", { name: "Apply dates" }).click();
  expect(new URL(page.url()).searchParams.get("start")).toBe(
    "2026-10-26T06:00:00.000Z",
  );
  expect(new URL(page.url()).searchParams.get("end")).toBe(
    "2026-10-29T06:00:00.000Z",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("empty periods and failed refreshes retain useful recovery", async ({
  page,
}, info) => {
  const f = seed();
  let fail = true;
  await page.route("**/api/workspaces/*/usage?*", async (route) =>
    fail
      ? route.fulfill({ status: 503, json: { detail: "Read failed" } })
      : route.continue(),
  );
  await page.goto(f.workspace_url);
  const usage = page.getByRole("region", {
    name: "Workspace usage",
    exact: true,
  });
  await expect(usage.getByText(/Could not refresh usage/)).toBeVisible();
  fail = false;
  await usage.getByRole("button", { name: "Retry usage" }).click();
  await expect(usage.locator("dd").first()).toHaveText("4,800");
  await usage.getByLabel("From", { exact: true }).fill("2020-01-01");
  await usage.getByLabel("Through", { exact: true }).fill("2020-01-02");
  await usage.getByRole("button", { name: "Apply dates" }).click();
  await expect(usage).toContainText("No usage in this period.");
  await dialogViews(page, info, "empty-usage");
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

test("invalid saved dates recover without losing the selected task", async ({
  page,
}) => {
  const f = seed();
  for (const broken of ["start=not-a-date", "timezone=Not/AZone"]) {
    const query = new URLSearchParams(
      new URL(f.workspace_url, "http://localhost").search,
    );
    const [key, value] = broken.split("=");
    query.set(key, value);
    query.set("task_id", f.task);
    await page.goto(`/workspaces/${f.workspace}/usage?${query}`);
    const usage = page.getByRole("region", {
      name: "Workspace usage",
      exact: true,
    });
    await expect(
      usage.getByRole("button", { name: "Reset dates" }),
    ).toBeVisible();
    await usage.getByRole("button", { name: "Reset dates" }).click();
    await expect(usage.getByLabel("Usage period")).toHaveValue("today");
    await expect(usage.getByLabel("Usage task")).toHaveValue(f.task);
    await expect(
      usage.getByRole("button", { name: "Reset dates" }),
    ).toHaveCount(0);
  }
});
