import { expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { test, state, checkout, projectWorkspace } from "./support";
import { dialogViews } from "./dialog-evidence";

test("a result observation remains available during review and preserves a stale draft", async ({
  page,
  request,
}, info) => {
  const seed = JSON.parse(
    execFileSync(
      join(checkout, ".venv/bin/python"),
      [
        join(checkout, "scripts/create-integration-demo.py"),
        join(state, "observation-fixture"),
        "--state",
        state,
        "--project-id",
        "observation",
      ],
      { encoding: "utf8" },
    ),
  );
  const workspace = await projectWorkspace(request, seed.project);
  const base = `/api/workspaces/${workspace}`;
  // UI state injection only: backend tests exercise actual running-review races.
  await page.route("**/input-eligibility*", async (route) => {
    const response = await route.fetch();
    const value = await response.json();
    await route.fulfill({
      json: { ...value, enabled: false, reason: "review_owns_result" },
    });
  });
  await page.goto(`/workspaces/${workspace}/results/${seed.result}`);
  await page
    .getByRole("button", { name: "Add observation", exact: true })
    .click();
  const input = page.getByRole("textbox", {
    name: "What you observed in this result",
  });
  await input.fill(
    "The preview starts correctly. Narrow-screen labels need attention.",
  );
  await expect(
    page.getByText("Task busy. Reply when processing finishes."),
  ).toHaveCount(0);
  await dialogViews(page, info, "observation-active");
  await page
    .getByRole("button", { name: "Save observation", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Add observation", exact: true }),
  ).toBeVisible();
  const result = await (
    await request.get(`${base}/results/${seed.result}`)
  ).json();
  expect(result.approved_at).toBeNull();
  await page
    .getByRole("button", { name: "Add observation", exact: true })
    .click();
  await input.fill("Keep this exact result note when context changes.");
  await request.post(`${base}/results/${seed.result}/prepare`, {
    data: { expected_revision: result.revision, author: "human" },
  });
  await expect(
    page.getByRole("button", { name: "Save observation", exact: true }),
  ).toBeDisabled();
  await expect(input).toHaveValue(
    "Keep this exact result note when context changes.",
  );
  await dialogViews(page, info, "observation-stale");
});
