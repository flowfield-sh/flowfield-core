import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";
import { openResource } from "./shell-navigation";

test("one task owns assembled code, reviewer sign-off and durable PDF/screenshots", async ({
  page,
  request,
}, testInfo) => {
  // This journey generates artifacts, integrates Git changes and inspects both layouts.
  test.setTimeout(60_000);
  const assets = join(state, "outcome-assets");
  mkdirSync(assets);
  const html = `<!doctype html><html><meta charset="utf-8"><style>body{font:18px system-ui;color:#24372e;background:#f5f7f1;padding:60px;max-width:720px}h1{font-size:44px}article{background:white;border-radius:12px;padding:28px}li{margin:14px 0}</style><h1>Harbor release overview</h1><p>Launch brief · October 2026</p><article><h2>Ready for the pilot</h2><ul><li>One place to track release work.</li><li>Readable reports and screenshots stay with each task.</li><li>Review the exact changes before delivery.</li></ul><p>Next: share the pilot with the project team.</p></article></html>`;
  writeFileSync(join(assets, "overview.html"), html);
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.setContent(html);
  await page.screenshot({ path: join(assets, "launch-preview.png") });
  await page.pdf({
    path: join(assets, "launch-brief.pdf"),
    format: "A4",
    printBackground: true,
  });
  const value = JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/task_outcomes_browser_fixture.py"), state, assets],
      { encoding: "utf8" },
    ),
  );
  const base = `/workspaces/${value.workspace}`;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/tasks/T-3`);
  await expect(page.getByLabel("Current task definition")).toBeVisible();
  await expect(page.locator(".entity-pane-footer")).toHaveCount(0);
  await page.goto(`${base}?inbox=delivered`);
  await expect(
    page.getByRole("tab", { name: "Needs me", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Delivered", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("tabpanel", { name: "Delivered", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "History", exact: true }),
  ).toBeVisible();
  const deliveries = page.getByRole("region", {
    name: "Delivered tasks",
    exact: true,
  });
  await expect(deliveries.locator("article")).toHaveCount(1);
  await expect(
    deliveries.getByRole("img", { name: "launch-preview.png", exact: true }),
  ).toBeVisible();
  await deliveries
    .getByRole("link", { name: "Open result in work tab" })
    .click();
  await expect(page).toHaveURL(new RegExp(`${base}/results/`));
  await expect(page.locator(".task-result")).toBeVisible();
  await page.goto(`${base}/tasks/T-1`);
  await expect(page.locator(".entity-pane-footer")).toHaveCount(0);
  await expect(
    page.locator('[aria-label="Task feed"] .task-result'),
  ).not.toBeVisible();
  await page.goto(`${base}?inbox=delivered`);
  await expect(deliveries.locator("article")).toHaveCount(1);
  await page.goto(`${base}/tasks`);
  await expect(page.locator(".task-card")).toHaveCount(3);
  await expect(
    page.locator(".task-card").filter({ hasText: "Release API" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "Milestones", exact: true }),
  ).toHaveCount(0);
  await page
    .getByTestId("work")
    .getByRole("link", { name: /T-2 Build the release overview/ })
    .click();
  await expect(page).toHaveURL(`${base}/tasks/T-2`);
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail.getByRole("list", { name: "Task feed" })).toBeVisible();
  await expect(
    detail.getByRole("img", { name: "Observation recorded", exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("Testing observation · Result #1", { exact: true }),
  ).toBeVisible();
  const observation = detail.locator(
    '[data-kind="reply"] details.activity-entry',
  );
  await expect(observation).not.toHaveAttribute("open");
  await observation.locator(":scope > summary").click();
  await expect(observation).toHaveAttribute("open");
  await observation.locator(":scope > summary").click();
  await expect(observation).not.toHaveAttribute("open");
  await expect(
    detail.locator(".detail-header").getByText("Project:"),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("recorded-observation.png"),
  });
  await expect(detail.locator('[data-kind="review"]')).toContainText(
    "Reviewing result #1",
  );
  await expect(
    detail.locator('[data-kind="review"] details.activity-entry'),
  ).not.toHaveAttribute("open");
  const reviewerOutput = page.waitForResponse(
    (response) =>
      response.url().includes("/reviews/") &&
      response.url().includes("/attempts/") &&
      response.url().endsWith("/activity"),
  );
  await detail
    .locator('[data-kind="review"] details.activity-entry > summary')
    .click();
  expect((await reviewerOutput).ok()).toBe(true);
  await expect(
    detail.locator("summary").filter({ hasText: /^Reviewer ·/ }),
  ).toHaveCount(0);

  await expect(
    detail.locator(".work-identity-title").filter({ hasText: /^Release API$/ }),
  ).toBeVisible();
  await expect(
    detail
      .locator(".work-identity-title")
      .filter({ hasText: /^Capture deliverables$/ }),
  ).toBeVisible();
  await detail
    .locator('[data-kind="result"] details.activity-entry > summary')
    .click();
  await expect(
    detail.getByRole("img", { name: "launch-preview.png", exact: true }),
  ).not.toBeVisible();
  const resultEvent = detail.locator('[data-kind="result"]');
  await expect(resultEvent).not.toContainText("Supporting files");
  await expect(resultEvent).not.toContainText("Reviewer:");
  await expect(
    resultEvent.getByRole("link", { name: "Access results" }),
  ).toBeVisible();
  const assemblyEvent = detail
    .locator('[data-kind="attempt"]')
    .filter({ hasText: "Task assembled" });
  await expect(assemblyEvent).toHaveCount(1);
  await expect(assemblyEvent.locator("summary")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("assembled-ready.png") });
  const waiting = await (
    await request.get(`/api${base}/tasks/${value.dependent}`)
  ).json();
  expect(waiting.blocked_by).toHaveLength(1);
  await detail
    .getByRole("link", { name: "Access results", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Run result", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Harbor", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Run command", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "launch-preview.png", exact: true }),
  ).not.toBeVisible();
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  const resultId = page.url().split("/").at(-1)!;
  const resultPath = `/api${base}/results/${resultId}`;
  const oldResult = await (await request.get(resultPath)).json();
  let submitted = false;
  let releaseSave!: () => void;
  const saveGate = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  await page.route(`**${resultPath}`, (route) =>
    submitted ? route.fulfill({ json: oldResult }) : route.continue(),
  );
  await page.route(`**${resultPath}/review`, async (route) => {
    submitted = true;
    await saveGate;
    await route.fulfill({ response: await route.fetch() });
  });
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  const saving = page.getByRole("button", {
    name: "Approve and integrate",
    exact: true,
  });
  await expect(saving).toHaveText("Saving…");
  await expect(saving).toBeDisabled();
  releaseSave();
  await expect(saving).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Cancel delivery" }),
  ).toHaveCount(0);
  // Old GET responses cannot bring back approval controls after the mutation receipt.
  await expect(
    page.getByRole("button", { name: "Request changes", exact: true }),
  ).toHaveCount(0);
  await page.unroute(`**${resultPath}`);
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/tasks/${value.feature}`)).json())
          .status,
      { timeout: 15_000 },
    )
    .toBe("done");
  expect(
    execFileSync("git", ["-C", value.repository, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim(),
  ).toBe(value.candidate);
  expect(
    (await (await request.get(`/api${base}/tasks/${value.dependent}`)).json())
      .blocked_by,
  ).toEqual([]);
  await expect(
    detail.getByText("Integrating changes", { exact: true }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("button", { name: "Cancel delivery", exact: true }),
  ).toHaveCount(0);

  await expect(detail.getByText(/This assessment is outdated/)).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("assembled-delivered.png"),
  });
  await page.goto(`${base}?inbox=delivered`);
  await expect(deliveries.locator("article")).toHaveCount(2);
  await expect(deliveries.locator("article").first()).toContainText("T-2");
  await expect(deliveries.locator("article").first()).toContainText(
    "Integrated into main",
  );
  await expect(
    deliveries
      .locator("article")
      .first()
      .getByRole("link", { name: "Access results", exact: true }),
  ).toBeVisible();
  await expect(
    deliveries.locator("article").first().getByRole("img"),
  ).toHaveCount(0);
  const recent = page.getByRole("region", { name: "Delivered", exact: true });
  await expect(recent.locator(".work-navigation-row").first()).toContainText(
    "T-2",
  );
  await page.screenshot({ path: testInfo.outputPath("delivered-desktop.png") });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("delivered-narrow-dark.png"),
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(`${base}/tasks/${value.report}`);
  await detail
    .locator('[data-kind="result"] details.activity-entry > summary')
    .click();
  await detail.getByRole("link", { name: "Access results" }).click();
  await expect(
    page
      .getByRole("region", { name: "Proposed result", exact: true })
      .getByText("The outcome meets the task criteria.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /launch-brief.pdf/ }).first(),
  ).toBeVisible();
  await openResource(page, "Files");
  const files = page.getByRole("region", { name: "Files", exact: true });
  await files
    .getByRole("button", { name: /launch-brief.pdf/ })
    .first()
    .click();
  const inspector = files.getByRole("region", {
    name: "File details",
    exact: true,
  });
  await expect(inspector).toBeVisible();
  const download = await request.get(
    (await inspector
      .getByRole("link", { name: "Download", exact: true })
      .getAttribute("href")) as string,
  );
  expect((await download.body()).subarray(0, 4).toString()).toBe("%PDF");
  for (const [width, theme] of [
    [1440, "light"],
    [390, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: theme });
    if (theme === "dark")
      await expect(page.locator("html")).toHaveClass(/dark/);
    else await expect(page.locator("html")).not.toHaveClass(/dark/);
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`outputs-${width}-${theme}.png`),
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  const report = await (
    await request.get(`/api${base}/tasks/${value.report}`)
  ).json();
  const archived = await request.put(`/api${base}/tasks/${value.report}`, {
    data: { expected_revision: report.revision, archived: true },
  });
  expect(archived.ok()).toBe(true);
  await page.goto(`${base}/archive`);
  await expect(page.getByTestId("work")).toContainText("T-1");
  await page.goto(`${base}?inbox=delivered`);
  await expect(deliveries.locator("article")).toHaveCount(1);
  await expect(deliveries.locator("article")).toContainText("T-2");
});
