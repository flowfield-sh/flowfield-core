import { expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { test, state, checkout, projectWorkspace } from "./support";

test("result feedback has no passive observation action during review", async ({
  page,
  request,
}) => {
  const seed = JSON.parse(
    execFileSync(
      join(checkout, ".venv/bin/python"),
      [
        join(checkout, "scripts/create-integration-demo.py"),
        join(state, "feedback-fixture"),
        "--state",
        state,
        "--project-id",
        "feedback",
      ],
      { encoding: "utf8" },
    ),
  );
  const workspace = await projectWorkspace(request, seed.project);
  await page.route("**/input-eligibility*", async (route) => {
    const response = await route.fetch(),
      value = await response.json();
    await route.fulfill({
      json: { ...value, enabled: false, reason: "review_owns_result" },
    });
  });
  await page.goto(`/workspaces/${workspace}/results/${seed.result}`);
  await expect(
    page.getByRole("button", { name: "Add observation" }),
  ).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: /observed/i })).toHaveCount(0);
  await page.unroute("**/input-eligibility*");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Request changes", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Feedback for this result" }),
  ).toBeVisible();
  // Existing exact request-changes drafts still own feedback.
});
