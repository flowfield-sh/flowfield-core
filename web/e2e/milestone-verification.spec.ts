import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

function seed(project: string, verdict = "passed", rounds = 1) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        resolve("../tests/verification_browser_fixture.py"),
        state,
        project,
        verdict,
        String(rounds),
      ],
      { encoding: "utf8" },
    ),
  );
}

test("Inbox review accepts exact milestone evidence and keeps history", async ({
  page,
  request,
}, testInfo) => {
  const value = seed("verification-accept");
  const base = `/workspaces/${value.workspace_id}`;
  await page.goto(`${base}/inbox`);
  await page
    .locator(".request-card")
    .getByRole("link", { name: /Verify installed harness discovery/ })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("tabpanel", { name: "Review", exact: true })
      .getByRole("img", {
        name: "Ready for milestone review · 2 of 2 checks passed",
      }),
  ).toBeVisible();
  await page
    .getByRole("tabpanel", { name: "Review", exact: true })
    .getByText("Tasks", { exact: true })
    .click();
  const tasks = page
    .getByRole("tabpanel", { name: "Review", exact: true })
    .getByLabel("Verification tasks", { exact: true });
  await expect(tasks.getByRole("link")).toHaveCount(2);
  const record = await (
    await request.get(`/api${base}/verifications/${value.verification_id}`)
  ).json();
  for (const task of record.tasks) {
    await expect(
      tasks.getByRole("link", { name: task.task_key, exact: true }),
    ).toHaveAttribute(
      "href",
      `/projects/${task.project_id}/tasks/${task.task_key}/result/${task.result_id}`,
    );
  }
  await expect(tasks).toContainText("Verification");
  await expect(tasks).not.toContainText("aaaaaaaaaaaa");
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`milestone-review-${width}-${theme}.png`),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page
    .getByRole("button", { name: "Accept milestone", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("milestone-review-actions-narrow.png"),
  });
  await page
    .getByRole("button", { name: "Accept milestone", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Milestone accepted", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
  const inbox = await (await request.get(`/api${base}/view/attention`)).json();
  expect(
    inbox.items.filter((item: { kind: string }) => item.kind === "milestone"),
  ).toHaveLength(0);
  await expect(page.locator(".entity-overlay-footer")).toBeEmpty();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Milestone accepted", exact: true }),
  ).toBeVisible();
});

test("request changes preserves feedback and stale evidence cannot be accepted", async ({
  page,
  request,
}) => {
  const value = seed("verification-changes");
  const base = `/workspaces/${value.workspace_id}`;
  await page.goto(
    `${base}/milestones/${value.milestone_key}?verification=${value.verification_id}`,
  );
  await page
    .getByRole("textbox", { name: "Review note" })
    .fill("Please test cold installation too.");
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Changes requested", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Please test cold installation too.", { exact: true }),
  ).toBeVisible();
  const stale = seed("verification-stale");
  const staleBase = `/workspaces/${stale.workspace_id}`;
  await page.goto(
    `${staleBase}/milestones/${stale.milestone_key}?verification=${stale.verification_id}`,
  );
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeVisible();
  await request.put(`/api${staleBase}/milestones/${stale.milestone_id}`, {
    data: {
      expected_revision: 1,
      acceptance_criteria: "Also test a cold installation.",
    },
  });
  await expect(
    page
      .locator(".entity-overlay-footer")
      .getByText(/revision 1; revision 2 is now current/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
});

test("a completed verifier with a failed criterion never offers acceptance", async ({
  page,
}) => {
  const value = seed("verification-failed", "failed");
  await page.goto(`/workspaces/${value.workspace_id}/milestones`);
  const progress = page.getByRole("region", { name: "In progress milestones" });
  await expect(
    progress.getByText("1 of 2 checks passed", { exact: true }),
  ).toBeVisible();
  await progress
    .getByRole("link", {
      name: "Verify installed harness discovery",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/tab=review/);
  await expect(
    page
      .getByRole("list", { name: "Checks" })
      .getByRole("img", { name: "Failed", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".entity-overlay-footer")
      .getByText(
        "Ask the Coordinator to address the findings and verify the milestone again.",
        { exact: true },
      ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
});

test("external review refreshes the pane without losing an unsent note", async ({
  page,
  request,
}) => {
  const value = seed("verification-concurrent");
  const base = `/workspaces/${value.workspace_id}`;
  await page.goto(
    `${base}/milestones/${value.milestone_key}?verification=${value.verification_id}`,
  );
  await page
    .getByRole("textbox", { name: "Review note" })
    .fill("My unsent observation");
  const current = await (
    await request.get(`/api${base}/verifications/${value.verification_id}`)
  ).json();
  expect(
    (
      await request.post(
        `/api${base}/verifications/${value.verification_id}/review`,
        { data: { expected_revision: current.revision, action: "accept" } },
      )
    ).ok(),
  ).toBeTruthy();
  await expect(
    page.getByRole("heading", { name: "Milestone accepted", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Review note" })).toHaveValue(
    "My unsent observation",
  );
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      "This request no longer needs a response. Your unsent note is preserved.",
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Discard note", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
});

test("long milestone history scrolls by wheel and preserves reading through refresh", async ({
  page,
  request,
}) => {
  const value = seed("verification-scroll", "passed", 12);
  const base = `/workspaces/${value.workspace_id}`;
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${base}/milestones/${value.milestone_key}?tab=review`);
    await expect(
      page.getByRole("button", { name: "Accept milestone", exact: true }),
    ).toBeVisible();
    const pane = page.locator(".entity-pane .entity-overlay-body");
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeGreaterThan(500);
    const bounds = (await pane.boundingBox())!;
    await page.mouse.move(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height / 2,
    );
    const initial = await pane.evaluate((n) => n.scrollTop);
    await page.mouse.wheel(0, -450);
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeLessThan(initial - 200);
    const before = await pane.evaluate((n) => n.scrollTop);
    // A harmless metadata refresh must not pull the reader back to the selected report.
    const current = await (await request.get(`/api${base}`)).json();
    const refreshed = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/workspaces") &&
        response.request().method() === "GET",
    );
    const response = await request.put(`/api${base}`, {
      data: {
        expected_revision: current.revision,
        name: `History at ${width}`,
      },
    });
    expect(response.ok(), await response.text()).toBe(true);
    await refreshed;
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeGreaterThan(before - 5);
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeLessThan(before + 5);
    await page.mouse.wheel(0, 400);
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeGreaterThan(before + 200);
    await page.mouse.wheel(0, -20000);
    await expect.poll(() => pane.evaluate((n) => n.scrollTop)).toBeLessThan(5);
    await page
      .getByRole("button", { name: "Earlier reviews", exact: true })
      .click();
    await expect(
      page
        .getByRole("list", { name: "Milestone review feed" })
        .locator(":scope > li"),
    ).toHaveCount(12);
    await page.mouse.move(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height / 2,
    );
    const loaded = await pane.evaluate((n) => n.scrollTop);
    await page.mouse.wheel(0, 400);
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeGreaterThan(loaded + 200);
    const older = await page
      .getByRole("list", { name: "Milestone review feed" })
      .locator(":scope > li")
      .first()
      .getAttribute("data-message-id");
    await page.goto(
      `${base}/milestones/${value.milestone_key}?verification=${older}`,
    );
    await expect(
      page.getByRole("link", { name: "Latest review", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".entity-overlay-footer")).toBeEmpty();
  }
});
