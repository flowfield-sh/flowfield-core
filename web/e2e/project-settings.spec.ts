import {
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

test("Project adoption updates the browser, task creation and initial connection recovery", async ({
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
  const adopted = await (
    await fixtureProject(page.request, {
      data: { path: existingDirectory(join(state, "browser")) },
    })
  ).json();
  await page.getByLabel("Workspace", { exact: true }).click();
  await page.getByRole("option", { name: adopted.name, exact: true }).click();
  const workspace = await projectWorkspace(page.request, adopted.id);
  await expect(page).toHaveURL(`/workspaces/${workspace}/inbox`);
  await page.getByRole("link", { name: "Task Board", exact: true }).click();
  expect(
    (
      await request.post(`/api/workspaces/${workspace}/tasks`, {
        data: {
          title: "Investigate large exports",
          labels: ["investigation"],
        },
      })
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

test("project tabs save independently and place scoped validation below actions", async ({
  page,
  request,
}) => {
  const project = "tabbed-settings",
    repo = join(state, project);
  await fixtureProject(request, { data: { path: existingDirectory(repo) } });
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
  await page.goto(`/projects/${project}/edit/setup`);
  const panel = () =>
    page
      .getByRole("region", { name: "Edit project", exact: true })
      .locator('[role="tabpanel"]:not([hidden])');
  const tab = async (name: string) => {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(panel()).toBeVisible();
  };
  const save = async () => {
    await panel()
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    await expect(
      panel().getByRole("button", { name: "Save changes", exact: true }),
    ).toBeDisabled();
  };
  await expect(panel().locator("details")).toHaveCount(0);
  await expect(
    panel().getByRole("heading", { name: "Setup validation", exact: true }),
  ).toHaveCount(0);
  await panel()
    .getByLabel("Setup commands", { exact: true })
    .fill("echo prepared");
  await panel().getByLabel("Time limit per command (seconds)").fill("15");
  await tab("Validation");
  await panel()
    .getByLabel("Validation commands", { exact: true })
    .fill("echo test-failure; exit 4");
  await tab("Delivery");
  await panel().getByLabel("Destination branch").fill("delivery");
  await save();
  await tab("Setup");
  await expect(
    panel().getByLabel("Setup commands", { exact: true }),
  ).toHaveValue("echo prepared");
  await save();
  // Saving another tab rebases only our own unchanged revision; the Validation draft survives.
  await tab("Validation");
  await expect(
    panel().getByLabel("Validation commands", { exact: true }),
  ).toHaveValue("echo test-failure; exit 4");
  await save();
  await tab("Setup");
  await panel().getByRole("button", { name: "Validate", exact: true }).click();
  await expect(
    panel().getByRole("heading", { name: "Setup validation", exact: true }),
  ).toBeVisible();
  await expect(
    panel().getByRole("heading", { name: "Validation passed", exact: true }),
  ).toBeVisible();
  const passedNotice = panel()
    .locator(".status-notice")
    .filter({
      has: page.getByRole("heading", {
        name: "Validation passed",
        exact: true,
      }),
    });
  // A passed command with a checkout warning still needs its explanatory body.
  await expect(passedNotice).toContainText("Switch the project checkout");
  expect(git("symbolic-ref", "--short", "HEAD")).toBe("main");
  git("switch", "delivery");
  await panel().getByRole("button", { name: "Validate", exact: true }).click();
  await expect(passedNotice).toBeVisible();
  await expect(
    passedNotice.locator('[data-slot="alert-description"]'),
  ).toBeHidden();
  const padding = await passedNotice.evaluate((element) => {
    const title = element
      .querySelector('[data-slot="alert-title"]')!
      .getBoundingClientRect();
    const bounds = element.getBoundingClientRect();
    return {
      top: title.top - bounds.top,
      bottom: bounds.bottom - title.bottom,
    };
  });
  expect(Math.abs(padding.top - padding.bottom)).toBeLessThan(1);
  const actions = await panel()
    .getByRole("button", { name: "Validate", exact: true })
    .boundingBox();
  const observed = await panel()
    .getByRole("heading", { name: "Setup validation", exact: true })
    .boundingBox();
  expect(observed!.y).toBeGreaterThan(actions!.y + actions!.height);
  await tab("Validation");
  await expect(
    panel().getByRole("heading", { name: "Validation results", exact: true }),
  ).toHaveCount(0);
  await panel().getByRole("button", { name: "Validate", exact: true }).click();
  await expect(
    panel().getByRole("heading", { name: "Validation failed", exact: true }),
  ).toBeVisible();
  await expect(
    panel().getByText("test-failure", { exact: true }),
  ).toBeVisible();
  await tab("Setup");
  await expect(
    panel().getByRole("heading", { name: "Validation passed", exact: true }),
  ).toBeVisible();
  await tab("Execution");
  await panel().getByLabel("Result execution").selectOption("command");
  await expect(
    panel().getByRole("checkbox", {
      name: "Prepare new results automatically",
    }),
  ).toBeChecked();
  await expect(panel().getByLabel("Result URL")).toHaveCount(0);
  await expect(panel().locator("details")).toHaveCount(0);
  await panel().getByLabel("Run command", { exact: true }).fill("echo result");
  let attempts = 0;
  await page.route(
    `**/api/projects/${project}/inspection/settings`,
    (route) => {
      if (route.request().method() === "PUT" && ++attempts === 1)
        return route.fulfill({
          status: 503,
          json: { error: { message: "Execution save interrupted" } },
        });
      return route.continue();
    },
  );
  await panel()
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Execution save interrupted");
  await expect(panel().getByLabel("Run command", { exact: true })).toHaveValue(
    "echo result",
  );
  await save();
  const integration = await (
    await request.get(`/api/projects/${project}/integration`)
  ).json();
  expect(integration).toMatchObject({
    target_branch: "delivery",
    setup_commands: ["echo prepared"],
    setup_timeout_seconds: 15,
    checks: ["echo test-failure; exit 4"],
  });
  expect(git("rev-parse", "refs/heads/delivery")).toBe(head);
  expect(git("symbolic-ref", "--short", "HEAD")).toBe("delivery");
  await panel().getByLabel("Result execution").selectOption("none");
  await expect(
    panel().getByRole("button", { name: "Save changes", exact: true }),
  ).toBeDisabled();
  await panel()
    .getByLabel("Reason", { exact: true })
    .fill("Library verified through tests.");
  await save();
  await page.reload();
  await expect(panel().getByLabel("Reason", { exact: true })).toHaveValue(
    "Library verified through tests.",
  );
  expect(
    (await request.get(`/api/projects/${project}/check-permission`)).status(),
  ).toBe(404);
});
