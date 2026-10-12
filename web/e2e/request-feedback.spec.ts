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
        task_prefix: `F${kind.slice(0, 2).toUpperCase()}`,
      },
    });
    expect(created.ok()).toBe(true);
    if (kind === "timeout")
      await page.addInitScript(() => {
        const original = AbortSignal.timeout.bind(AbortSignal);
        AbortSignal.timeout = (ms) => original(ms === 10000 ? 500 : ms);
      });
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
    const failure = errors(page);
    await expect(failure).toContainText("Could not save project settings");
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

test("an outage is reported once across settings reads and polling, recovers, and reports a later outage", async ({
  page,
  request,
}, testInfo) => {
  const id = "feedback-offline";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)), task_prefix: "FOF" },
  });
  let offline = false;
  let releaseReads!: () => void;
  const heldReads = new Promise<void>((resolve) => {
    releaseReads = resolve;
  });
  const pending = new Set<string>();
  // Settings mount before their tab opens. Hold these two reads so the outage
  // reliably affects both the form and validation status, regardless of timing.
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (
      path === `/api/projects/${id}/inspection/settings` ||
      path === `/api/projects/${id}/setup-validation`
    ) {
      pending.add(path);
      await heldReads;
    }
    return offline ? route.abort("failed") : route.continue();
  });
  await page.goto(`/projects/${id}/edit/general`);
  const editor = page.getByRole("region", {
    name: "Edit project",
    exact: true,
  });
  await expect(editor.getByLabel("Name", { exact: true })).toBeVisible();
  await expect.poll(() => pending.size).toBe(2);
  offline = true;
  releaseReads();
  await editor.getByRole("tab", { name: "Integration", exact: true }).click();
  const integration = editor.getByRole("region", {
    name: "Integration settings",
  });
  await expect(errors(page)).toContainText("Flowfield is unavailable");
  const retries = integration.getByRole("button", {
    name: "Retry loading",
    exact: true,
  });
  await expect(retries).toHaveCount(2);
  await expect(retries.nth(0)).toBeVisible();
  await expect(retries.nth(1)).toBeVisible();
  await expect(integration).not.toContainText(
    /Load failed|Cannot reach Flowfield/,
  );
  await atTopRight(page, errors(page));
  await page.screenshot({
    path: testInfo.outputPath("offline-integration-desktop.png"),
  });
  await errors(page).getByRole("button", { name: "Close toast" }).click();
  await expect(errors(page)).toHaveCount(0);
  await editor.getByRole("tab", { name: "General", exact: true }).click();
  await expect(editor.getByLabel("Name", { exact: true })).toBeVisible();
  // Observe two real notification polls: dismissed background failures stay dismissed.
  for (let i = 0; i < 2; i++)
    await page.waitForRequest("**/api/announcements/poll");
  await expect(errors(page)).toHaveCount(0);
  await editor.getByRole("tab", { name: "General", exact: true }).click();
  await editor
    .getByLabel("Name", { exact: true })
    .fill("Draft survives outage");
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(errors(page)).toContainText("Could not save project settings");
  await expect(errors(page)).not.toContainText("preserved");
  offline = false;
  await errors(page).getByRole("button", { name: "Close toast" }).click();
  await editor.getByRole("tab", { name: "Integration", exact: true }).click();
  // A recovered background read can clear another retry while the click is settling.
  for (let attempt = 0; attempt < 2 && (await retries.count()); attempt++) {
    try {
      await retries.first().click({ timeout: 1500 });
    } catch {
      await expect(retries).toHaveCount(0);
    }
  }
  await expect(retries).toHaveCount(0);
  await expect(
    integration.getByLabel("Validation commands", { exact: true }),
  ).toBeEnabled();
  await expect(
    integration.getByLabel("Run command", { exact: true }),
  ).toBeEnabled();
  offline = true;
  await expect(errors(page)).toContainText("Flowfield is unavailable", {
    timeout: 7000,
  });
  await editor.getByRole("tab", { name: "General", exact: true }).click();
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Draft survives outage",
  );
});

test("worker and coordinator settings report failed saves with their own action and retain values", async ({
  page,
  request,
}) => {
  const id = "feedback-models";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)), task_prefix: "FMD" },
  });
  const workspace = await projectWorkspace(request, id);
  await page.route(`**/api/workspaces/${workspace}/models*`, (route) =>
    route.fulfill({
      json: [{ id: "fixture", name: "Fixture", efforts: ["low"] }],
    }),
  );
  await page.goto(`/workspaces/${workspace}/edit/workers`);
  const workers = page.getByRole("region", { name: "Worker settings" });
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
  await page.goto(`/projects/${id}`);
  const settings = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await expect(settings).toBeVisible();
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
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(errors(page)).toHaveCount(0);
  await page.route("**/api/**", (route) => route.abort());
  await page.reload();
  await expect(errors(page)).toContainText("Flowfield is unavailable");
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toBeVisible();
  await expect(page.locator("main")).not.toContainText("Load failed");
});

test("a saved correction is not offered for resubmission when the following read fails", async ({
  page,
  request,
}) => {
  const id = "feedback-correction";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)), task_prefix: "FCR" },
  });
  const question = {
    id: "scope",
    project_id: id,
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
    applied_project_revision: null,
    applied_task_revisions: {},
    created_at: "2026-10-10T00:00:00Z",
    updated_at: "2026-10-10T00:00:00Z",
    updated_by: "human",
  };
  let saved = 0;
  await page.route(`**/api/projects/${id}/questions/scope`, (route) =>
    saved ? route.abort() : route.fulfill({ json: question }),
  );
  await page.route(
    `**/api/projects/${id}/questions/scope/correction`,
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
  await page.goto(`/projects/${id}/inbox/scope`);
  const detail = page.getByRole("region", { name: "Question", exact: true });
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
    `/workspaces/${await projectWorkspace(request, id)}/projects/${id}/inbox/corrected`,
  );
  await expect(detail.getByLabel("Your answer", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    detail.getByRole("button", { name: "Reload question" }),
  ).toBeVisible();
  expect(saved).toBe(1);
});
