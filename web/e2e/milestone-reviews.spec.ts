import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

function seed(kind: string) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/milestone_review_browser_fixture.py"), state, kind],
      { encoding: "utf8" },
    ),
  );
}

test("combined native-review evidence ends at exact human promotion", async ({
  page,
  request,
}, testInfo) => {
  const value = seed("ready");
  const path = `/workspaces/${value.workspace}/milestones/${value.milestone}?verification=${value.verification}`;
  await page.goto(path);
  await expect(
    page.getByRole("button", { name: "Accept and promote", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".editor-heading")).not.toContainText("→");
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Delivery destination", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Final approval delivers the verified result to", {
      exact: false,
    }),
  ).toContainText("main");
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await page.locator(".verification-checks summary").first().click();
  await expect(
    page.getByText("The feature file exists", { exact: false }),
  ).toBeVisible();
  await page.getByText("Tasks", { exact: true }).last().click();
  await expect(
    page.getByLabel("Verification tasks").getByRole("link"),
  ).toHaveCount(1);
  await page
    .getByText("Review rounds and diagnostics", { exact: true })
    .click();
  await expect(page.getByText("Reviewer ·", { exact: false })).toBeVisible();
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await expect
      .poll(() =>
        page
          .locator("html")
          .evaluate((element) => element.classList.contains("dark")),
      )
      .toBe(theme === "dark");
    await page.screenshot({
      path: testInfo.outputPath(`combined-review-${width}-${theme}.png`),
      fullPage: true,
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page
    .getByRole("button", { name: "Accept and promote", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(
              `/api/workspaces/${value.workspace}/verifications/${value.verification}`,
            )
          ).json()
        ).status,
    )
    .toBe("accepted");
  await expect(
    page.getByRole("button", { name: "Accept and promote", exact: true }),
  ).toHaveCount(0);
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await expect(
      page.getByRole("heading", { name: "Milestone accepted" }),
    ).toBeVisible();
    await expect
      .poll(() =>
        page
          .locator("html")
          .evaluate((element) => element.classList.contains("dark")),
      )
      .toBe(theme === "dark");
    await page.screenshot({
      path: testInfo.outputPath(`completed-review-${width}-${theme}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
});

test("failed milestone reviewer has shared recovery and a paused retry", async ({
  page,
  request,
}, testInfo) => {
  const value = seed("failed");
  const attention = await (
    await request.get(`/api/workspaces/${value.workspace}/view/attention`)
  ).json();
  expect(
    attention.items.some(
      (item: { id: string }) => item.id === value.verification,
    ),
  ).toBe(true);
  await page.goto(
    `/workspaces/${value.workspace}/milestones/${value.milestone}?verification=${value.verification}`,
  );
  await expect(
    page.getByRole("button", { name: "Retry review", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accept and promote", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("combined-review-failed.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Retry review", exact: true }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(
              `/api/projects/${value.project}/milestone-reviews/${value.review}`,
            )
          ).json()
        ).status,
    )
    .toBe("queued");
  await expect(
    page.getByRole("button", { name: "Stop review", exact: true }),
  ).toBeVisible();
});

test("milestone review question reuses the canonical answer and exact evidence link", async ({
  page,
  request,
}) => {
  const value = seed("question");
  await page.goto(
    `/workspaces/${value.workspace}/milestones/${value.milestone}?verification=${value.verification}`,
  );
  await page
    .getByRole("link", { name: "Answer milestone review question" })
    .click();
  await expect(
    page.getByText("Clarify the expected result text", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("Use the wording already recorded in the task.");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  const review = await (
    await request.get(
      `/api/projects/${value.project}/milestone-reviews/${value.review}`,
    )
  ).json();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(
              `/api/projects/${value.project}/questions/${review.question_id}`,
            )
          ).json()
        ).status,
    )
    .toBe("applied");
});
