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
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("tabpanel", { name: "Activity", exact: true })
      .getByRole("img", {
        name: "Ready for milestone review · 2 of 2 checks passed",
      }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("tabpanel", { name: "Activity", exact: true })
      .getByRole("heading", { name: "Tasks", exact: true }),
  ).toBeVisible();
  const tasks = page
    .getByRole("tabpanel", { name: "Activity", exact: true })
    .getByLabel("Verification tasks", { exact: true });
  await expect(tasks.getByRole("link")).toHaveCount(2);
  const record = await (
    await request.get(`/api${base}/verifications/${value.verification_id}`)
  ).json();
  for (const task of record.tasks) {
    await expect(
      tasks
        .getByRole("link")
        .filter({ has: page.getByText(task.task_key, { exact: true }) }),
    ).toHaveAttribute(
      "href",
      `/projects/${task.project_id}/tasks/${task.task_key}/result/${task.result_id}`,
    );
    await expect(tasks).toContainText(record.task_titles[task.result_id]);
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
  await expect(page).toHaveURL(/tab=activity/);
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

test("milestone Activity keeps expandable revisions, paged reviews and exact notes", async ({
  page,
}) => {
  const value = seed("verification-scroll", "passed", 22);
  const base = `/workspaces/${value.workspace_id}/milestones/${value.milestone_key}`;
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${base}?tab=activity`);
    await expect(
      page.getByRole("button", { name: "Accept milestone", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("tablist", { name: "Milestone views" }).getByRole("tab"),
    ).toHaveText(["Overview", "Activity", "Settings"]);
    const feed = page.getByRole("list", { name: "Milestone activity" });
    await expect(feed.locator(":scope > li")).toHaveCount(20);
    await expect(feed.locator(".activity-entry[open]")).toHaveCount(1);
    const note = page.getByRole("textbox", { name: "Review note" });
    await note.fill(`Keep this exact report note at ${width}`);
    await page
      .getByRole("button", { name: "Earlier activity", exact: true })
      .click();
    await expect(feed.locator(":scope > li")).toHaveCount(23);
    const definition = feed.locator('[data-kind="revision"]').first();
    await definition.locator("summary").click();
    await expect(
      definition.getByRole("heading", {
        name: "Acceptance criteria",
        exact: true,
      }),
    ).toBeVisible();
    const old = feed.locator('[data-kind="review"]').first();
    await old.locator(".activity-entry > summary").click();
    await expect(
      old.getByText("Review round 1: include the recovery walkthrough.", {
        exact: true,
      }),
    ).toBeVisible();
    const href = await old
      .locator(".activity-entry-meta a:has(time)")
      .getAttribute("href");
    await old.locator(".activity-entry-meta a:has(time)").click();
    await expect(
      page.getByRole("button", { name: "Accept milestone", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("link", { name: "Unsent review note", exact: true })
      .click();
    await expect(note).toHaveValue(`Keep this exact report note at ${width}`);
    await note.fill("");
    await page.goto(href!);
    await expect(
      old.getByText("Review round 1: include the recovery walkthrough.", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator(".entity-overlay-footer")).toBeEmpty();
  }
});
