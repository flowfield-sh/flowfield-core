import {
  fixtureCliProject,
  projectWorkspace,
  fixtureProject,
  choose,
  test,
  existingDirectory,
  state,
} from "./support";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";

test("CLI adoption updates the browser, task creation and initial connection recovery", async ({
  page,
  request,
}) => {
  await page.route("**/api/workspaces", (route) => route.abort());
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toBeVisible();
  await page.unroute("**/api/workspaces");
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(
    page.getByRole("link", { name: "New workspace", exact: true }),
  ).toHaveCount(1);
  const adopted = fixtureCliProject([
    existingDirectory(join(state, "browser")),
  ]);
  await page.getByLabel("Workspace", { exact: true }).click();
  await page.getByRole("option", { name: adopted.name, exact: true }).click();
  const workspace = await projectWorkspace(page.request, adopted.id);
  await expect(page).toHaveURL(`/workspaces/${workspace}/inbox`);
  await page.getByRole("link", { name: "Task Board", exact: true }).click();
  expect(
    (
      await request.post(
        `/api/workspaces/${await projectWorkspace(page.request, adopted.id)}/tasks`,
        {
          data: {
            title: "Investigate large exports",
            labels: ["investigation"],
          },
        },
      )
    ).ok(),
  ).toBeTruthy();
  await expect(
    page
      .getByRole("region", {
        name: "Backlog",
        exact: true,
      })
      .getByRole("link", {
        name: /Investigate large exports/,
      }),
  ).toBeVisible();
  const tasks = await (
    await request.get(
      `/api/workspaces/${await projectWorkspace(page.request, "browser")}/tasks`,
    )
  ).json();
  expect(tasks[0].labels).toEqual(["investigation"]);
  expect(tasks[0].id).toMatch(/^investigate-large-exports-/);
  await page.reload();
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: /Investigate large exports/ }),
  ).toBeVisible();
});

test("worker models load automatically and retry without replacing setting drafts", async ({
  page,
  request,
}) => {
  const project = "model-discovery";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, project)) },
  });
  const workspace = await projectWorkspace(page.request, project);
  let calls = 0;
  let refreshCalls = 0;
  await page.route(`**/api/workspaces/${workspace}/models*`, async (route) => {
    calls++;
    if (new URL(route.request().url()).searchParams.get("refresh") !== "true")
      return route.fulfill({
        status: 503,
        json: { error: { message: "Catalog temporarily unavailable" } },
      });
    if (new URL(route.request().url()).searchParams.get("refresh") === "true")
      refreshCalls++;
    return route.fulfill({
      json: [
        { id: "model-one", name: "Model one", efforts: ["low", "high"] },
        { id: "model-two", name: "Model two", efforts: ["medium"] },
        { id: "no-effort", name: "No effort control", efforts: [] },
      ],
    });
  });
  await page.goto(`/workspaces/${workspace}/edit/workers`);
  const settings = page.getByRole("region", { name: "Worker settings" });
  await expect.poll(() => calls).toBe(2);
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Catalog temporarily unavailable");
  await expect(settings).not.toContainText("Catalog temporarily unavailable");
  await expect(
    settings.getByRole("button", { name: "Retry loading models" }),
  ).toBeEnabled();
  await settings.getByLabel("Maximum parallel workers").fill("3");
  await settings.getByRole("button", { name: "Retry loading models" }).click();
  const model = settings.getByLabel("Model", { exact: true });
  const effort = settings.getByLabel("Reasoning effort");
  await expect(model).toBeEnabled();
  await model.click();
  await expect(
    page.getByRole("option", { name: "Model one", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(model).toHaveAttribute("data-value", "test-model");
  await expect(settings.getByLabel("Maximum parallel workers")).toHaveValue(
    "3",
  );
  await choose(model, "model-one");
  await choose(effort, "high");
  await choose(model, "model-two");
  await expect(effort).toHaveAttribute("data-value", "");
  await effort.click();
  await expect(
    page.getByRole("option", { name: "high", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await choose(effort, "medium");
  await choose(model, "no-effort");
  await expect(effort).toHaveCount(0);
  let captured: unknown;
  await page.route(`**/api/workspaces/${workspace}/workers`, async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    const body = route.request().postDataJSON();
    captured = body;
    await route.fulfill({
      json: {
        workspace_id: workspace,
        revision: 2,
        enabled: false,
        problem: null,
        selection: body.selection,
        max_parallel: body.max_parallel,
      },
    });
  });
  await settings.getByRole("button", { name: "Save worker settings" }).click();
  await expect
    .poll(() => captured)
    .toEqual({
      expected_revision: 1,
      auto_start_queue: false,
      max_parallel: 3,
      selection: {
        harness: "codex",
        model: "no-effort",
        effort: null,
        mode: null,
        fast: false,
      },
    });
  expect(calls).toBeGreaterThan(1);
  expect(refreshCalls).toBe(1);
});

test("integration settings create an explicit local target without changing the human checkout", async ({
  page,
  request,
}, testInfo) => {
  const project = "integration-settings";
  const repo = join(state, project);
  await fixtureProject(request, {
    data: { path: existingDirectory(repo) },
  });
  const workspace = await projectWorkspace(page.request, project);
  const git = (...args: string[]) =>
    execFileSync(
      "git",
      ["-C", repo, "-c", "core.hooksPath=/dev/null", ...args],
      { encoding: "utf8" },
    ).trim();
  git("init", "-b", "main");
  git("add", ".");
  git(
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@example.invalid",
    "commit",
    "-m",
    "base",
  );
  const head = git("rev-parse", "HEAD");
  await page.goto(`/projects/${project}/edit`);
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  const settings = page.getByRole("region", {
    name: "Integration settings",
    exact: true,
  });
  await settings
    .getByLabel("Destination branch", { exact: true })
    .fill("integration");
  await expect(
    settings.getByRole("checkbox", { name: "Create this branch" }),
  ).toHaveCount(0);
  await expect(settings.getByLabel("Executable paths")).toHaveCount(0);
  await expect(settings.getByLabel("Seconds per setup command")).toBeVisible();
  await settings
    .getByLabel("Validation commands", { exact: true })
    .fill("python -m unittest -v");
  await settings
    .getByLabel("Run command", { exact: true })
    .fill("python app.py");
  let runSaves = 0;
  await page.route(
    `**/api/projects/${project}/inspection/settings`,
    (route) => {
      if (route.request().method() === "PUT" && ++runSaves === 1)
        return route.fulfill({
          status: 503,
          json: { error: { message: "Run command save interrupted" } },
        });
      return route.continue();
    },
  );
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/projects/${project}/integration`) &&
      response.request().method() === "PUT",
  );
  await settings
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  expect((await saved).ok()).toBe(true);
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Run command save interrupted");
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Integration saved; run command save was not confirmed");
  await expect(settings.getByLabel("Run command", { exact: true })).toHaveValue(
    "python app.py",
  );
  // Integration was saved; retry only the pending run command without revising delivery settings.
  const runSaved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/projects/${project}/inspection/settings`) &&
      response.request().method() === "PUT",
  );
  await settings
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  expect((await runSaved).ok()).toBe(true);
  await expect(
    settings.getByRole("button", {
      name: "Save integration settings",
      exact: true,
    }),
  ).toBeDisabled();
  expect(
    (await (await request.get(`/api/projects/${project}/integration`)).json())
      .revision,
  ).toBe(2);
  expect(
    (
      await (
        await request.get(`/api/projects/${project}/inspection/settings`)
      ).json()
    ).run_command,
  ).toBe("python app.py");
  // Deterministic UI projection; backend tests execute real Local setup commands.
  await page.route(
    `**/api/projects/${project}/setup-validation`,
    async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      expect(route.request().postDataJSON().expected_revision).toBe(2);
      await route.fulfill({
        json: {
          project_id: project,
          id: "setup-check",
          settings_revision: 2,
          commit: head,
          created_at: new Date().toISOString(),
          status: "failed",
          stale: false,
          problem: "The required compiler is unavailable in this environment.",
          checkout_problem:
            "Switch the project checkout to 'integration', then retry integration. Local files are unchanged.",
          setup: [
            {
              command: "compiler --version",
              exit_code: 127,
              output: "compiler: not found",
              truncated: false,
            },
          ],
          checks: [],
          workspace: null,
          pid: null,
          commands: [],
        },
      });
    },
  );
  await settings.getByRole("button", { name: "Validate saved setup" }).click();
  await expect(
    settings.getByText(
      "The required compiler is unavailable in this environment.",
    ),
  ).toBeVisible();
  await expect(
    settings.getByText("compiler: not found", { exact: true }),
  ).toBeVisible();
  await expect(
    settings
      .getByRole("status")
      .filter({ hasText: "Checkout is not ready for delivery" }),
  ).toContainText("Switch the project checkout");
  const validationStatus = settings
    .getByRole("status")
    .filter({ hasText: "Project setup validation failed" });
  await expect(
    validationStatus.getByRole("button", { name: "Validate saved setup" }),
  ).toBeEnabled();
  if (process.env.FLOWFIELD_VISUAL_EVIDENCE) {
    await validationStatus.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath("integration-validation-desktop.png"),
      animations: "disabled",
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await validationStatus.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath("integration-validation-mobile.png"),
      animations: "disabled",
    });
  }
  await settings.getByLabel("Setup commands").fill("install-dependencies");
  await expect(
    settings.getByRole("button", { name: "Validate saved setup" }),
  ).toBeDisabled();
  await settings.getByLabel("Setup commands").fill("");
  expect(git("rev-parse", "refs/heads/integration")).toBe(head);
  expect(git("symbolic-ref", "--short", "HEAD")).toBe("main");
  await page.reload();
  await expect(
    settings.getByLabel("Destination branch", { exact: true }),
  ).toHaveValue("integration");
  await expect(
    settings.getByLabel("Validation commands", { exact: true }),
  ).toHaveValue("python -m unittest -v");
  expect((await request.get(`/api/workspaces/${workspace}/workers`)).ok()).toBe(
    true,
  );
  expect(
    (await (await request.get(`/api/workspaces/${workspace}/workers`)).json())
      .enabled,
  ).toBe(false);
});
