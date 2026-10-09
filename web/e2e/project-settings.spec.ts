import { fixtureProgress } from "./support";
import { fixtureStages } from "./support";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import { test, existingDirectory, state, cli, connectMcp } from "./support";

test("CLI adoption updates the browser, task creation and initial connection recovery", async ({
  page,
  request,
}) => {
  await page.route("**/api/projects", (route) => route.abort());
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toBeVisible();
  await page.unroute("**/api/projects");
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(
    page.getByRole("region", { name: "Project setup instructions" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add project", exact: true }),
  ).toHaveCount(1);
  const adopted = cli([
    "project",
    "init",
    existingDirectory(join(state, "browser")),
  ]);
  await page.getByRole("link", { name: adopted.name, exact: true }).click();
  await expect(page).toHaveURL("/projects/" + adopted.id);
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Title", exact: true })
    .fill("Investigate large exports");
  await page
    .getByRole("combobox", { name: "Type", exact: true })
    .selectOption("investigation");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await page.getByRole("button", { name: "Back to board" }).click();
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
  const tasks = await (await request.get("/api/projects/browser/tasks")).json();
  expect(tasks[0].task_type).toBe("investigation");
  expect(tasks[0].id).toMatch(/^investigate-large-exports-/);
  await page.reload();
  await expect(
    page.getByRole("link", {
      name: /Investigate large exports/,
    }),
  ).toBeVisible();
});

test("milestones group tasks with linked details and long project intent stays out of the shell", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "milestone-trial";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "MIL",
  });
  const description = "Enduring project intent. ".repeat(500);
  await call("edit_project", {
    project_id,
    changes: { expected_revision: 1, description },
  });
  const milestone = await call("create_milestone", {
    project_id,
    milestone: {
      id: "export",
      title: "CSV export",
      body: "Deliver a useful export.",
    },
  });
  expect(milestone.key).toBe("M-1");
  expect(
    (await call("get_milestone", { project_id, milestone_id: "M-1" })).id,
  ).toBe("export");
  expect(cli(["milestone", "show", "M-1", "--project", project_id]).key).toBe(
    "M-1",
  );
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "serializer",
      title: "Serialize CSV",
      milestone_id: "M-1",
    },
  });
  await fixtureProgress(request, {
    project_id,
    task_id: "serializer",
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "download",
      title: "Download CSV",
      milestone_id: "M-1",
    },
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "unrelated",
      title: "Independent bug",
      task_type: "bug",
    },
  });
  await page.goto(`/projects/${project_id}`);
  await expect(page.getByText(description, { exact: true })).toHaveCount(0);

  const badge = page
    .locator(".task-card")
    .filter({ hasText: "Download CSV" })
    .getByRole("link", { name: "M-1", exact: true });
  await badge.hover();
  await expect(page.getByRole("tooltip")).toHaveText("CSV export");
  await badge.focus();
  await expect(page.getByRole("tooltip")).toHaveText("CSV export");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await badge.click();
  await expect(page).toHaveURL(new RegExp(`/milestones/M-1$`));
  await expect(page.locator(".board")).toBeVisible();
  const editor = page.getByRole("region", {
    name: "Edit milestone",
    exact: true,
  });
  await expect(editor).toContainText("Deliver a useful export.");

  await editor.getByRole("button", { name: "Edit", exact: true }).click();
  await editor
    .getByLabel("Description", { exact: true })
    .fill("Preserved milestone draft");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(editor.getByLabel("Description", { exact: true })).toHaveValue(
    "Preserved milestone draft",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await page.reload();
  await expect(editor).toContainText("Preserved milestone draft");
  await page.goto(`/projects/${project_id}/milestones/export`);
  await expect(editor).toContainText("Preserved milestone draft");
  // Historical tab URLs remain valid aliases of the simplified detail.
  await page.goto(`/projects/${project_id}/milestones/export/tasks`);
  await expect(editor).toContainText("Preserved milestone draft");
  await page.getByRole("button", { name: "View tasks", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project_id}`);
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("export");
  await expect(page.locator(".task-card")).toHaveCount(2);
  await expect(page.locator(".board")).toContainText("Serialize CSV");
  await expect(page.locator(".board")).not.toContainText("Independent bug");
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  const project = page.getByRole("region", {
    name: "Edit project",
    exact: true,
  });
  await expect(project.getByText(description, { exact: true })).toBeVisible();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
  await expect(page.getByText(description, { exact: true })).toHaveCount(0);
});

test("worker models load explicitly and retry without replacing setting drafts", async ({
  page,
  request,
}) => {
  const project = "model-discovery";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "MDL" },
  });
  let calls = 0;
  let refreshCalls = 0;
  await page.route("**/api/worker-models*", async (route) => {
    calls++;
    if (calls === 1)
      return route.fulfill({
        status: 503,
        json: { error: { message: "Catalog temporarily unavailable" } },
      });
    refreshCalls++;
    return route.fulfill({
      json: [
        { id: "model-one", name: "Model one", efforts: ["low", "high"] },
        { id: "model-two", name: "Model two", efforts: ["medium"] },
        { id: "no-effort", name: "No effort control", efforts: [] },
      ],
    });
  });
  await page.goto(`/projects/${project}/edit`);
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  const settings = page.getByRole("region", { name: "Worker settings" });
  expect(calls).toBe(0);
  await settings
    .getByRole("button", { name: "Load models", exact: true })
    .click();
  await expect(settings).toContainText("Catalog temporarily unavailable");
  await expect(
    settings.getByRole("button", { name: "Refresh models" }),
  ).toHaveCount(0);
  await settings.getByLabel("Maximum parallel workers").fill("3");
  await settings.getByRole("button", { name: "Reload models" }).click();
  const model = settings.getByLabel("Model", { exact: true });
  const effort = settings.getByLabel("Reasoning effort");
  await expect(model.getByRole("option", { name: "Model one" })).toBeAttached();
  await expect(model).toHaveValue("");
  await expect(settings.getByLabel("Maximum parallel workers")).toHaveValue(
    "3",
  );
  await model.selectOption("model-one");
  await effort.selectOption("high");
  await model.selectOption("model-two");
  await expect(effort).toHaveValue("");
  await expect(
    effort.getByRole("option", { name: "high", exact: true }),
  ).toHaveCount(0);
  await effort.selectOption("medium");
  await model.selectOption("no-effort");
  await expect(effort).toHaveCount(0);
  let captured: unknown;
  await page.route(`**/api/projects/${project}/workers`, async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    const body = route.request().postDataJSON();
    captured = body;
    await route.fulfill({
      json: {
        project_id: project,
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
}) => {
  const project = "integration-settings";
  const repo = join(state, project);
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(repo), task_prefix: "IGS" },
  });
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
  await expect(settings).toContainText("Run command save interrupted");
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
      .getByRole("alert")
      .filter({ hasText: "Checkout is not ready for delivery" }),
  ).toContainText("Switch the project checkout");
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
  expect((await request.get(`/api/projects/${project}/workers`)).ok()).toBe(
    true,
  );
  expect(
    (await (await request.get(`/api/projects/${project}/workers`)).json())
      .enabled,
  ).toBe(false);
});
