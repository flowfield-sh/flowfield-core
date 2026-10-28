import { expect } from "@playwright/test";
import { test } from "./support";
import { usageFixture, tokens, unknownTokens } from "./usage-fixtures";
import { dialogViews } from "./dialog-evidence";

async function setup(request: import("@playwright/test").APIRequestContext) {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Usage review",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "fixture" },
          ]),
        ),
      },
    })
  ).json();
  const task = await (
    await request.post(`/api/workspaces/${workspace.id}/tasks`, {
      data: {
        title: "Trace recorded work",
        body: "Usage during implementation and review.",
      },
    })
  ).json();
  return {
    workspace: workspace.id,
    task,
    url: `/workspaces/${workspace.id}/tasks/${task.key}`,
  };
}

test("task usage shows one clock and compact role figures without accounting controls", async ({
  page,
  request,
}, info) => {
  const f = await setup(request),
    view = usageFixture(f.workspace, f.task.id);
  await page.route("**/tasks/*/usage", (route) =>
    route.fulfill({ json: view }),
  );
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("3,800");
  await expect(usage.locator("dd").nth(1)).toHaveText("20m 0s");
  await expect(usage.getByLabel("worker usage")).toContainText("3,500");
  await expect(usage.getByLabel("reviewer usage")).toContainText("300");
  await expect(usage.locator("details, table")).toHaveCount(0);
  await expect(
    usage.getByText(/coverage|accounting|recorded execution/i),
  ).toHaveCount(0);
  await usage.scrollIntoViewIfNeeded();
  await dialogViews(page, info, "task-usage");
});

test("live tokens refresh, parallel time advances once, and completion stops the clock", async ({
  page,
  request,
}) => {
  const f = await setup(request),
    view = usageFixture(f.workspace, f.task.id);
  view.measured_at = new Date().toISOString();
  view.totals.elapsed_seconds = 60;
  view.totals.live_intervals = 2;
  view.breakdowns[0].live = true;
  view.breakdowns[1].live = true;
  let reads = 0;
  await page.route("**/tasks/*/usage", (route) => {
    reads++;
    return route.fulfill({ json: view });
  });
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage.getByText("Running", { exact: true })).toBeVisible();
  await expect(usage.locator("dd").nth(1)).toHaveText(/1m [0-9]s/);
  view.totals.tokens = tokens(5800);
  await expect(usage.locator("dd").first()).toHaveText("5,800", {
    timeout: 10000,
  });
  expect(reads).toBeGreaterThan(1);
  view.measured_at = new Date().toISOString();
  view.totals.elapsed_seconds = 75;
  view.totals.live_intervals = 0;
  view.breakdowns.forEach((row) => (row.live = false));
  await expect(usage.getByText("Running", { exact: true })).toHaveCount(0, {
    timeout: 10000,
  });
  await expect(usage.locator("dd").nth(1)).toHaveText("1m 15s");
});

test("failed reads retry and unknown usage never becomes measured zero", async ({
  page,
  request,
}, info) => {
  const f = await setup(request),
    view = usageFixture(f.workspace, f.task.id);
  let fail = true;
  view.totals.tokens = unknownTokens;
  view.totals.elapsed_seconds = null;
  await page.route("**/tasks/*/usage", (route) =>
    fail
      ? route.fulfill({ status: 503, json: { detail: "Temporary failure" } })
      : route.fulfill({ json: view }),
  );
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage.getByText(/Could not refresh/)).toBeVisible();
  fail = false;
  await usage.getByRole("button", { name: "Retry task usage" }).click();
  await expect(usage.locator("dd").first()).toHaveText("—");
  await expect(usage.locator("dd").nth(1)).toHaveText("—");
  view.totals.tokens = tokens(0);
  view.totals.elapsed_seconds = 0;
  await page.reload();
  await expect(usage.locator("dd").first()).toHaveText("0");
  await expect(usage.locator("dd").nth(1)).toHaveText("0s");
  await usage.scrollIntoViewIfNeeded();
  await dialogViews(page, info, "measured-zero");
});

test("new tasks show a short empty state", async ({ page, request }) => {
  const f = await setup(request);
  await page.goto(f.url);
  const usage = page.getByRole("region", { name: "Task usage", exact: true });
  await expect(usage).toContainText("No usage yet.");
  await expect(usage.locator("dd")).toHaveCount(0);
});
