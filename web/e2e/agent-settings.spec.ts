import {
  fixtureProject,
  projectWorkspace,
  choose,
  test,
  stubResourcePresence,
} from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";

test("task settings cancel dismissed edits, reject stale saves and reset; tool answers survive reload", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "agent-settings");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await fixtureProject(request, {
        data: { path },
      })
    ).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, "agent-settings");
  const task = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, "agent-settings")}/tasks`,
      {
        data: { title: "Check project behavior" },
      },
    )
  ).json();
  const first = {
    harness: "codex",
    model: "first",
    effort: "low",
    mode: "workspace-write",
    fast: false,
  };
  const modes = [
    {
      id: "workspace-write",
      name: "Workspace access",
      description: "Ask before accessing the network.",
    },
    {
      id: "read-only",
      name: "Read-only",
      description: "Ask before editing files.",
    },
  ];
  let settings = {
    revision: 1,
    selection: null as typeof first | null,
    effective: {
      choice: first,
      source: "workspace",
      default_revision: 1,
      override_revision: 1,
    },
  };
  await page.route(
    "**/api/projects/agent-settings/integration",
    async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        json: { ...(await response.json()), runtime: "local" },
      });
    },
  );
  let releaseModels!: () => void;
  const discovery = new Promise<void>((resolve) => {
    releaseModels = resolve;
  });
  await page.route(
    "**/api/{worker-models,workspaces/*/models}*",
    async (route) => {
      await discovery;
      await route.fulfill({
        json: [
          { id: "first", name: "First", efforts: ["low", "high"], modes },
          {
            id: "second",
            name: "Second",
            efforts: ["medium"],
            modes,
            fast: true,
            fast_description: "Faster responses, increased usage",
          },
        ],
      });
    },
  );
  await page.route("**/agent-settings", async (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON();
      expect(payload.expected_revision).toBe(settings.revision);
      settings = {
        revision: settings.revision + 1,
        selection: payload.selection,
        effective: {
          choice: payload.selection ?? first,
          source: payload.selection ? "override" : "workspace",
          default_revision: 1,
          override_revision: settings.revision + 1,
        },
      };
    }
    await route.fulfill({ json: settings });
  });
  const longPermissionLabel =
    "Yes, and don't ask again for commands that start with `node -e '" +
    'fetch("http://127.0.0.1:8902/pocket-list.js");'.repeat(8) +
    'console.log("complete-prefix")\'`';
  let permission = {
    id: "request-1",
    project_id: "agent-settings",
    workspace_id: workspace,
    role: "worker",
    task_id: task.id,
    run_id: "run",
    conversation_id: null,
    binding: "binding",
    session_id: "session",
    turn_id: "turn",
    tool_id: "check",
    title: "Run the project checks",
    details: "command: pnpm test\ncwd: /project/worktree",
    options: [
      { id: "allow", label: "Allow once", kind: "allow_once" },
      { id: "future", label: longPermissionLabel, kind: "allow_always" },
      { id: "deny", label: "Reject once", kind: "reject_once" },
    ],
    revision: 1,
    status: "pending",
    answer: null as string | null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 300000).toISOString(),
    released_at: null as string | null,
  };
  await page.route(`**/api/workspaces/*/permissions?*`, (route) =>
    route.fulfill({
      json: {
        pending: permission.status === "pending" ? [permission] : [],
        items: permission.status === "pending" ? [] : [permission],
        next_before: null,
      },
    }),
  );
  await page.route("**/permissions/request-1/answer", async (route) => {
    const body = route.request().postDataJSON();
    expect(body).toEqual({ expected_revision: 1, option_id: "deny" });
    permission = {
      ...permission,
      revision: 2,
      status: "answered",
      answer: body.option_id,
      released_at: new Date().toISOString(),
    };
    await route.fulfill({ json: permission });
  });
  await page.goto(`/workspaces/${workspace}/tasks/${task.key}/settings`);
  const detail = page.getByRole("region", { name: "Task details" });
  const picker = detail.getByRole("region", { name: "Worker", exact: true });
  for (const label of ["Model", "Reasoning effort"]) {
    const select = picker.getByRole("combobox", { name: label, exact: true });
    await expect(select).toBeDisabled();
    await expect(select).toHaveText(/^Loading/);
    await expect(select).not.toContainText("unavailable");
  }
  releaseModels();
  await expect(picker.getByLabel("Model", { exact: true })).toHaveAttribute(
    "data-value",
    "first",
  );
  await choose(picker.getByLabel("Model", { exact: true }), "second");
  await expect(picker.getByLabel("Reasoning effort")).toHaveAttribute(
    "data-value",
    "",
  );
  await choose(picker.getByLabel("Reasoning effort"), "medium");
  await choose(picker.getByLabel("Access mode"), "read-only");
  await expect(
    picker.getByRole("button", { name: "Fast mode", exact: true }),
  ).toHaveCount(0);

  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    // Radix repositions on the next frame after a viewport change.
    await expect
      .poll(async () => {
        const tops = await Promise.all(
          (width === 1280
            ? ["Save", "Reset", "Refresh models"]
            : ["Save", "Reset"]
          ).map(
            async (name) =>
              (await picker
                .getByRole("button", { name, exact: true })
                .boundingBox())!.y,
          ),
        );
        return Math.max(...tops) - Math.min(...tops);
      })
      .toBeLessThan(1);
    const bounds = await Promise.all(
      ["Save", "Reset", "Refresh models"].map(
        async (name) =>
          (await picker
            .getByRole("button", { name, exact: true })
            .boundingBox())!,
      ),
    );
    for (let index = 0; index < bounds.length; index++) {
      const box = bounds[index];
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      if (index) {
        const previous = bounds[index - 1];
        expect(box.y > previous.y || box.x > previous.x).toBe(true);
      }
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await picker.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(picker.getByLabel("Model", { exact: true })).toHaveAttribute(
    "data-value",
    "first",
  );
  await expect(picker.getByLabel("Reasoning effort")).toHaveAttribute(
    "data-value",
    "low",
  );
  await choose(picker.getByLabel("Model", { exact: true }), "second");
  await choose(picker.getByLabel("Reasoning effort"), "medium");
  await choose(picker.getByLabel("Access mode"), "read-only");
  await picker.getByLabel("Access mode").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/flowfield-slice3-native-modes.png" });
  await picker
    .getByRole("checkbox", { name: "Fast mode", exact: true })
    .check();
  await picker.getByRole("button", { name: "Save" }).click();
  await expect(
    picker.getByRole("checkbox", { name: "Fast mode", exact: true }),
  ).toBeChecked();

  await expect(
    picker.getByText("Overrides workspace defaults for the next worker run.", {
      exact: true,
    }),
  ).toBeVisible();
  expect(settings.selection?.mode).toBe("read-only");
  expect(settings.selection?.fast).toBe(true);
  await choose(picker.getByLabel("Model", { exact: true }), "first");
  await choose(picker.getByLabel("Reasoning effort"), "high");
  settings = { ...settings, revision: settings.revision + 1 };
  await request.post(
    `/api/workspaces/${await projectWorkspace(request, "agent-settings")}/tasks`,
    {
      data: { title: "Cause settings refresh" },
    },
  );
  await expect(
    picker.getByText(
      "Settings changed elsewhere. Load the latest settings before saving.",
    ),
  ).toBeVisible();
  await expect(picker.getByLabel("Reasoning effort")).toHaveAttribute(
    "data-value",
    "high",
  );
  await expect(picker.getByRole("button", { name: "Save" })).toBeDisabled();
  await expect(
    picker.getByRole("button", { name: "Reset", exact: true }),
  ).toBeDisabled();
  page.once("dialog", (dialog) => dialog.accept());
  await picker.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(picker.getByLabel("Model", { exact: true })).toHaveAttribute(
    "data-value",
    "second",
  );
  await picker.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(picker).toBeVisible();
  await expect(picker.getByLabel("Model", { exact: true })).toHaveAttribute(
    "data-value",
    "first",
  );
  await expect(picker.getByLabel("Reasoning effort")).toHaveAttribute(
    "data-value",
    "low",
  );
  await expect(picker.getByLabel("Access mode")).toHaveAttribute(
    "data-value",
    "workspace-write",
  );
  await expect(
    picker.getByRole("button", { name: "Reset", exact: true }),
  ).toBeDisabled();
  await expect(
    picker.getByRole("button", { name: "Save", exact: true }),
  ).toBeDisabled();
  expect(settings.selection).toBeNull();
  expect(settings.effective.choice.fast).toBe(false);
  await expect(
    picker.getByRole("button", { name: "Cancel", exact: true }),
  ).toHaveCount(0);
  for (const [width, colorScheme] of [
    [1280, "light"],
    [390, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme });
    await page.screenshot({
      path: `/tmp/flowfield-worker-reset-${width}.png`,
      animations: "disabled",
    });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ colorScheme: "light" });
  await expect(
    picker.getByText("Overrides workspace defaults for the next worker run.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(picker).toBeVisible();
  expect(settings.selection).toBeNull();
  await detail.getByRole("tab", { name: "Task", exact: true }).click();
  await expect(detail.locator(".entity-pane-footer")).toHaveCount(0);
  await page.route(
    "**/api/workspaces/*/requests/permission/request-1*",
    (route) =>
      route.fulfill({
        json: {
          id: permission.id,
          workspace_id: workspace,
          project_id: "agent-settings",
          task_id: task.id,
          task_key: task.key,
          kind: "permission",
          request_kind: "permission",
          title: permission.title,
          status: permission.status,
          updated_at: permission.updated_at,
        },
      }),
  );
  await page.route("**/permissions/request-1?*", (route) =>
    route.fulfill({ json: permission }),
  );
  await stubResourcePresence(page);
  await page.goto(`/workspaces/${workspace}/requests/permission/request-1`);
  const permissionDetail = page.getByRole("region", {
    name: "Request",
    exact: true,
  });
  await permissionDetail
    .getByText("Run the project checks", { exact: true })
    .scrollIntoViewIfNeeded();
  await expect(
    permissionDetail.getByText("command: pnpm test", { exact: false }),
  ).toBeVisible();
  const longChoice = permissionDetail.getByRole("button", {
    name: longPermissionLabel,
    exact: true,
  });
  await expect(longChoice).toBeVisible();
  await expect(longChoice).toHaveText(longPermissionLabel);
  await page.setViewportSize({ width: 390, height: 844 });
  await longChoice.scrollIntoViewIfNeeded();
  expect(
    await longChoice.evaluate(
      (button) =>
        button.scrollWidth <= button.clientWidth && button.clientHeight > 32,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "/tmp/flowfield-permission-mobile.png" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: "/tmp/flowfield-slice3-settings.png" });
  await permissionDetail.getByRole("button", { name: "Reject once" }).click();
  await expect(
    permissionDetail.getByRole("button", { name: "Allow once" }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    permissionDetail.getByText("Tool permission history", { exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/flowfield-slice2-mobile.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route(
    `**/api/workspaces/${workspace}/coordinator-settings`,
    (route) =>
      route.fulfill({
        json: { revision: 1, selection: null, effective: null },
      }),
  );
  await page.goto("/projects/agent-settings/edit/coordinator");
  const coordinator = page.getByRole("tabpanel", {
    name: "Coordinator",
    exact: true,
  });
  await expect(coordinator.getByLabel("Model", { exact: true })).toHaveCount(0);
  permission = {
    ...permission,
    id: "request-2",
    revision: 1,
    status: "pending",
    answer: null,
    released_at: null,
  };
  await page.route(
    `**/api/workspaces/${workspace}/view/attention?*`,
    (route) => {
      const column = new URL(route.request().url()).searchParams.get("column");
      const shown =
        column === (permission.status === "pending" ? "action" : "history");
      return route.fulfill({
        json: {
          items: shown
            ? [
                {
                  id: permission.id,
                  project_id: "agent-settings",
                  workspace_id: workspace,
                  task_id: task.id,
                  kind: "permission",
                  request_href: `/workspaces/${workspace}/requests/permission/${permission.id}?project_id=agent-settings`,
                  task_key: task.key,
                  title: permission.title,
                  status: permission.status,
                  updated_at: permission.updated_at,
                },
              ]
            : [],
          total: shown ? 1 : 0,
          next_offset: null,
        },
      });
    },
  );
  await page.route(
    `**/api/workspaces/${workspace}/requests/permission/request-2?*`,
    (route) =>
      route.fulfill({
        json: {
          id: permission.id,
          project_id: "agent-settings",
          workspace_id: workspace,
          kind: "permission",
          task_key: task.key,
          title: permission.title,
          status: permission.status,
          updated_at: permission.updated_at,
          request_kind: "permission",
          request_href: `/workspaces/${workspace}/requests/permission/request-2?project_id=agent-settings`,
          source_href: `/workspaces/${workspace}/tasks/${task.key}`,
        },
      }),
  );
  await page.route("**/permissions/request-2?*", (route) =>
    route.fulfill({ json: permission }),
  );
  await page.route("**/permissions/request-2/answer", async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      expected_revision: 1,
      option_id: "allow",
    });
    permission = {
      ...permission,
      revision: 2,
      status: "answered",
      answer: "allow",
      released_at: new Date().toISOString(),
    };
    // A real permission answer emits a project change. The intercepted answer
    // must also exercise that stream instead of relying on unrelated activity.
    expect(
      (
        await request.post(`/api/workspaces/${workspace}/tasks`, {
          data: {
            title: "Record scripted permission receipt",
          },
        })
      ).ok(),
    ).toBe(true);
    return route.fulfill({ json: permission });
  });
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  const action = page.getByRole("tabpanel", {
    name: "Needs me",
    exact: true,
  });
  await expect(
    action.getByRole("link", {
      name: `${task.key}: Run the project checks`,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    action.getByRole("button", { name: "Allow once" }),
  ).toBeVisible();
  await page.screenshot({ path: "/tmp/flowfield-slice2-attention.png" });
  await action.getByRole("button", { name: "Allow once" }).click();
  await expect(action.getByRole("button", { name: "Allow once" })).toHaveCount(
    0,
  );
  await expect(action.getByRole("article")).toHaveCount(0);
  await page.getByRole("tab", { name: "History", exact: true }).click();
  const history = page.getByRole("tabpanel", { name: "History", exact: true });
  await expect(
    history.getByRole("link", { name: /Run the project checks.*Answered/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    history.getByRole("link", { name: /Run the project checks.*Answered/ }),
  ).toBeVisible();
  await history
    .getByRole("link", { name: /Run the project checks.*Answered/ })
    .click();
  await expect(page.getByText(/Allow once · Answer recorded/)).toBeVisible();
});

test("Integration uses Local automatically and preserves drafts across project tabs", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "local-adoption");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await fixtureProject(request, {
        data: { path },
      })
    ).ok(),
  ).toBe(true);
  let settings = {
    project_id: "local-adoption",
    revision: 1,
    runtime: "local",
    target_branch: "main",
    checks: ["pnpm test"],
    setup_commands: ["pnpm install --frozen-lockfile"],
    setup_timeout_seconds: 120,
    check_timeout_seconds: 60,
    environment: {
      tools: { pnpm: "/old/pnpm" },
      read_paths: [],
      variables: { OLD: "retained" },
    },
  };
  await page.route(
    "**/api/projects/local-adoption/integration",
    async (route) => {
      if (route.request().method() === "PUT") {
        expect(route.request().postDataJSON()).not.toHaveProperty("runtime");
        expect(route.request().postDataJSON()).not.toHaveProperty(
          "environment",
        );
        settings = { ...settings, revision: 2, runtime: "local" };
      }
      await route.fulfill({ json: settings });
    },
  );
  await page.goto("/projects/local-adoption/edit/integration");
  const selection = page.getByLabel("Use Local host");
  await expect(selection).toHaveCount(0);
  await expect(page.getByLabel("Executable paths")).toHaveCount(0);
  await page.getByLabel("Validation commands").fill("pnpm check");
  await expect(page.getByLabel("Executable paths")).toHaveCount(0);
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(page.getByLabel("Validation commands")).toHaveValue(
    "pnpm check",
  );
  await page.getByRole("button", { name: "Save integration settings" }).click();
  await expect(selection).toHaveCount(0);
  await page.reload();
  await expect(selection).toHaveCount(0);

  await page.screenshot({ path: "/tmp/flowfield-slice3-local.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/flowfield-slice3-local-mobile.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
