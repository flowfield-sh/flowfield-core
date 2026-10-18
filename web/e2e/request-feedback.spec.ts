import {
  fixtureProject,
  projectWorkspace,
  test,
  existingDirectory,
  state,
  choose,
} from "./support";
import { join } from "node:path";
import { expect, type Locator, type Page } from "@playwright/test";

const errors = (page: Page) =>
  page.locator('[data-sonner-toast][data-type="error"]');
async function atTopRight(page: Page, toast: Locator) {
  await expect(toast).toBeVisible();
  await expect.poll(async () => (await toast.boundingBox())?.y).toBe(24);
  const bounds = (await toast.boundingBox())!;
  expect(bounds.x + bounds.width).toBe(page.viewportSize()!.width - 24);
}

for (const kind of [
  "network",
  "timeout",
  "invalid",
  "server",
  "conflict",
] as const) {
  test(`project save reports ${kind} failure without claiming a successful or untouched save`, async ({
    page,
    request,
  }) => {
    const id = `feedback-${kind}`;
    const created = await fixtureProject(request, {
      data: {
        path: existingDirectory(join(state, id)),
      },
    });
    expect(created.ok()).toBe(true);
    await page.goto(`/projects/${id}/edit/general`);
    const editor = page.getByRole("region", {
      name: "Edit project",
      exact: true,
    });
    await editor.getByLabel("Name", { exact: true }).fill("Unsaved name");
    await page.route(`**/api/projects/${id}`, async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      if (kind === "network") return route.abort("failed");
      if (kind === "timeout") return; // Deliberately withhold the response.
      if (kind === "invalid")
        return route.fulfill({
          status: 200,
          body: "<html>broken proxy</html>",
        });
      if (kind === "server") return route.fulfill({ status: 503, json: {} });
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "revision_conflict",
            message: "A newer project revision exists.",
          },
        },
      });
    });
    await editor
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    const failure = errors(page).filter({
      hasText: "Could not save project settings",
    });
    // Exercise the actual save deadline without shortening unrelated reads.
    await expect(failure).toBeVisible({
      timeout: kind === "timeout" ? 15000 : 5000,
    });
    await expect(failure).toContainText(
      {
        network: "Cannot reach Flowfield",
        timeout: "took too long",
        invalid: "unreadable response",
        server: "error (503)",
        conflict: "A newer project revision exists",
      }[kind],
    );
    await expect(failure).not.toContainText(
      /Load failed|Failed to fetch|edits are preserved/,
    );
    if (kind !== "conflict")
      await expect(failure).toContainText("check whether the action completed");
    else
      await expect(
        editor.getByRole("button", { name: "Load latest" }),
      ).toBeVisible();
    await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
      "Unsaved name",
    );
    await expect(
      editor.getByRole("button", { name: "Save changes", exact: true }),
    ).toBeEnabled();
    await expect(
      page.locator('[data-sonner-toast][data-type="success"]'),
    ).toHaveCount(0);
    await atTopRight(page, failure);
  });
}

test("worker and coordinator settings report failed saves with their own action and retain values", async ({
  page,
  request,
}) => {
  const id = "feedback-models";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  const workspace = await projectWorkspace(request, id);
  await page.route(`**/api/workspaces/${workspace}/models*`, (route) =>
    route.fulfill({
      json: [{ id: "fixture", name: "Fixture", efforts: ["low"] }],
    }),
  );
  await page.goto(`/workspaces/${workspace}/edit/workers`);
  const workers = page.getByRole("region", { name: "Worker settings" });
  await choose(workers.getByLabel("Harness", { exact: true }), "codex");
  await choose(workers.getByLabel("Model", { exact: true }), "fixture");
  await choose(workers.getByLabel("Reasoning effort"), "low");
  await workers.getByLabel("Maximum parallel workers").fill("3");
  await page.route(`**/api/workspaces/${workspace}/workers`, (route) =>
    route.request().method() === "PUT" ? route.abort() : route.continue(),
  );
  await workers.getByRole("button", { name: "Save worker settings" }).click();
  await expect(errors(page)).toContainText("Could not save worker settings");
  await expect(workers.getByLabel("Maximum parallel workers")).toHaveValue("3");
  await expect(workers).not.toContainText(/Load failed|Cannot reach Flowfield/);
  await errors(page).getByRole("button", { name: "Close toast" }).click();
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, id)}/tasks`,
  );
  const settings = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await expect(settings).toBeVisible();
  await choose(settings.getByLabel("Harness", { exact: true }), "codex");
  await choose(settings.getByLabel("Model", { exact: true }), "fixture");
  await choose(settings.getByLabel("Reasoning effort"), "low");
  await page.route(
    `**/api/workspaces/${workspace}/coordinator-settings`,
    (route) =>
      route.request().method() === "PUT" ? route.abort() : route.continue(),
  );
  await settings.getByRole("button", { name: "Save", exact: true }).click();
  await expect(errors(page)).toContainText("Could not save model settings");
  await expect(settings.getByLabel("Model", { exact: true })).toHaveAttribute(
    "data-value",
    "fixture",
  );
});

test("cancelled reads are silent and initial service failures have retry controls", async ({
  page,
}) => {
  let pending = false;
  await page.route("**/api/harnesses/codex", () => {
    pending = true;
  });
  await page.goto("/settings/harnesses");
  await expect.poll(() => pending).toBe(true);
  await page.getByRole("tab", { name: "Appearance", exact: true }).click();
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await expect(errors(page)).toHaveCount(0);
  await page.route("**/api/**", (route) => route.abort());
  await page.reload();
  await expect(
    page.getByRole("dialog", { name: "Connection lost" }),
  ).toBeVisible();
  await expect(errors(page)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Try reconnecting" }),
  ).toBeVisible();
  await expect(page.locator("main")).not.toContainText("Load failed");
});

test("a saved correction is not offered for resubmission when the following read fails", async ({
  page,
  request,
}) => {
  const id = "feedback-correction";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  const question = {
    id: "scope",
    workspace_id: await projectWorkspace(request, id),
    task_id: null,
    task_key: null,
    affected_task_ids: [],
    affected_task_keys: [],
    question: "Which scope?",
    context: "Choose the scope.",
    recommendation: "All records",
    choices: [],
    blocking_scope: null,
    author: "coordinator",
    status: "applied",
    revision: 3,
    answer: "All records",
    answer_count: 1,
    origin_run_id: null,
    continuation_run_id: "fixture-run",
    consumed_answer_revision: 2,
    correction_question_id: null,
    delivery: null,
    decision: "Agreed",
    applied_task_revision: null,
    applied_workspace_revision: null,
    applied_task_revisions: {},
    created_at: "2026-10-10T00:00:00Z",
    updated_at: "2026-10-10T00:00:00Z",
    updated_by: "human",
  };
  await page.route("**/api/workspaces/*/requests/question/scope*", (route) =>
    route.fulfill({
      json: {
        id: question.id,
        workspace_id: question.workspace_id,
        kind: "question",
        request_kind: "question",
        title: question.question,
        status: question.status,
        updated_at: question.updated_at,
        task_id: null,
        task_key: null,
        request_href: `/workspaces/${question.workspace_id}/requests/question/scope`,
        source_href: `/workspaces/${question.workspace_id}/inbox`,
      },
    }),
  );
  let saved = 0;
  await page.route(`**/api/workspaces/*/questions/scope`, (route) =>
    saved ? route.abort() : route.fulfill({ json: question }),
  );
  await page.route(
    `**/api/workspaces/*/questions/scope/correction`,
    (route) => {
      saved++;
      return route.fulfill({
        json: {
          ...question,
          id: "corrected",
          status: "answered",
          answer: "Only favourites",
        },
      });
    },
  );
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, id)}/requests/question/scope`,
  );
  const detail = page.getByRole("region", { name: "Request", exact: true });
  await detail
    .getByRole("button", { name: "Send correction", exact: true })
    .click();
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("Only favourites");
  await detail
    .getByRole("button", { name: "Send correction", exact: true })
    .click();
  await expect(errors(page)).toContainText(
    "Correction saved; could not refresh question",
  );
  await expect(
    detail.getByRole("link", { name: "Open the new input" }),
  ).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(page.request, id)}/requests/question/corrected`,
  );
  await expect(detail.getByLabel("Your answer", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    detail.getByRole("button", { name: "Reload question" }),
  ).toBeVisible();
  expect(saved).toBe(1);
});
