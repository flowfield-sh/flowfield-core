import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

function seed(project: string, verdict = "passed") {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        resolve("../tests/verification_browser_fixture.py"),
        state,
        project,
        verdict,
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
    .getByRole("link", { name: /Verify installed harness discovery/ })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Coverage: Complete")).toBeVisible();
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
    page.getByRole("heading", {
      name: "Verification out of date",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
});

test("a completed verifier with a failed criterion never offers acceptance", async ({
  page,
}) => {
  const value = seed("verification-failed", "failed");
  await page.goto(
    `/workspaces/${value.workspace_id}/milestones/${value.milestone_key}?verification=${value.verification_id}`,
  );
  await expect(
    page.getByRole("heading", {
      name: "Verification needs follow-up",
      exact: true,
    }),
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
  ).toBeDisabled();
  await page.getByRole("button", { name: "Discard note", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
});
