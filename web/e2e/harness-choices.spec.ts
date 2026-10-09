import { join } from "node:path";
import { expect } from "@playwright/test";
import {
  choose,
  test,
  hostStatus,
  existingDirectory,
  state,
  fixtureStages,
} from "./support";

for (const onlyClaude of [false, true]) {
  test(`worker choices support ${onlyClaude ? "Claude alone" : "independent supported harnesses"} with automatic scoped discovery`, async ({
    page,
    request,
  }) => {
    const project = onlyClaude
      ? "claude-only-choices"
      : "mixed-harness-choices";
    const initialized = await request.post("/api/projects/initialize", {
      data: {
        path: existingDirectory(join(state, project)),
        task_prefix: onlyClaude ? "KCL" : "MHC",
      },
    });
    const codex = hostStatus("codex", !onlyClaude);
    if (onlyClaude) {
      codex.native_installed = false;
      codex.problems = ["native_missing"];
    }
    await page.route("**/api/harnesses", (route) =>
      route.fulfill({ json: [codex, hostStatus("claude-code")] }),
    );
    const discoveries: string[] = [];
    await page.route("**/api/worker-models*", (route) => {
      const url = new URL(route.request().url());
      expect(url.searchParams.get("project_id")).toBe(project);
      expect(url.searchParams.get("refresh")).toBe("false");
      const kind = url.searchParams.get("harness")!;
      discoveries.push(kind);
      return route.fulfill({
        json:
          kind === "codex"
            ? [
                {
                  id: "codex-model",
                  name: "Codex model",
                  efforts: ["low"],
                  modes: [{ id: "workspace-write", name: "Workspace access" }],
                  fast: true,
                },
              ]
            : [
                {
                  id: "claude-sonnet-5-5",
                  name: "Sonnet 5.5",
                  efforts: [],
                  modes: [
                    {
                      id: "default",
                      name: "Default",
                      description: "Native Claude tool approval",
                    },
                  ],
                  fast: false,
                },
              ],
      });
      expect(initialized.ok()).toBe(true);
    });
    await page.goto(`/projects/${project}/edit/workers`);
    const form = page.getByRole("region", {
      name: "Worker settings",
      exact: true,
    });
    await expect(
      form.getByRole("combobox", { name: "Harness", exact: true }),
    ).toContainText(onlyClaude ? "Claude Code" : "Codex");
    await expect
      .poll(() => discoveries)
      .toEqual([onlyClaude ? "claude-code" : "codex"]);
    if (!onlyClaude) {
      await choose(form.getByLabel("Model", { exact: true }), "codex-model");
      await choose(form.getByLabel("Reasoning effort"), "low");
      await choose(form.getByLabel("Access mode"), "workspace-write");
      await form.getByLabel("Maximum parallel workers").fill("3");
      await form
        .getByRole("combobox", { name: "Harness", exact: true })
        .click();
      await page
        .getByRole("option", { name: "Claude Code", exact: true })
        .click();
      await expect(form.getByLabel("Model", { exact: true })).toHaveAttribute(
        "data-value",
        "",
      );
      await expect(form.getByLabel("Reasoning effort")).toHaveCount(0);
      await expect(form.getByLabel("Access mode")).toHaveCount(0);
      await expect.poll(() => discoveries).toEqual(["codex", "claude-code"]);
    }
    await choose(
      form.getByLabel("Model", { exact: true }),
      "claude-sonnet-5-5",
    );
    await expect(form.getByLabel("Reasoning effort")).toHaveCount(0);
    await choose(form.getByLabel("Access mode"), "default");
    await expect(form.getByRole("button", { name: "Fast mode" })).toHaveCount(
      0,
    );
    let captured: unknown;
    let savedSettings: unknown;
    let savedReads = 0;
    await page.route(`**/api/projects/${project}/workers`, (route) => {
      if (route.request().method() !== "PUT") {
        if (!savedSettings) return route.continue();
        savedReads++;
        return route.fulfill({ json: savedSettings });
      }
      const body = route.request().postDataJSON();
      captured = body;
      savedSettings = {
        project_id: project,
        revision: 2,
        enabled: false,
        problem: null,
        selection: body.selection,
        max_parallel: body.max_parallel,
      };
      return route.fulfill({ json: savedSettings });
    });
    await form
      .getByRole("button", { name: "Save worker settings", exact: true })
      .click();
    await expect
      .poll(() => captured)
      .toEqual({
        expected_revision: 1,
        max_parallel: onlyClaude ? 1 : 3,
        selection: {
          harness: "claude-code",
          model: "claude-sonnet-5-5",
          effort: null,
          mode: "default",
          fast: false,
        },
      });
    await expect(
      form.getByRole("button", { name: "Save worker settings", exact: true }),
    ).toBeDisabled();
    // A project event refreshes settings while their model menu remains open.
    // It must not close/reopen discovery or clear the saved model.
    expect(
      (
        await request.post(`/api/projects/${project}/tasks`, {
          data: { title: "Refresh project settings", stages: fixtureStages() },
        })
      ).ok(),
    ).toBe(true);
    await expect.poll(() => savedReads).toBeGreaterThan(0);
    await expect(form.getByLabel("Model", { exact: true })).toHaveAttribute(
      "data-value",
      "claude-sonnet-5-5",
    );
    // Saving the first defaults also activates the board's background preload.
    // The service coalesces/caches these reads; the open editor retains its model.
    expect(discoveries).toEqual(
      onlyClaude
        ? ["claude-code", "claude-code"]
        : ["codex", "claude-code", "claude-code"],
    );
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("Claude-only coordinator saves native choices independently of worker defaults without loading while closed", async ({
  page,
  request,
}, testInfo) => {
  const project = "claude-only-chat";
  const initialized = await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "KCC" },
  });
  expect(initialized.ok()).toBe(true);
  await page.route("**/api/harnesses", (route) =>
    route.fulfill({
      json: [hostStatus("codex", false), hostStatus("claude-code")],
    }),
  );
  let discoveries = 0;
  await page.route("**/api/worker-models*", (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("project_id")).toBe(project);
    expect(url.searchParams.get("harness")).toBe("claude-code");
    discoveries++;
    return route.fulfill({
      json: [
        {
          id: "claude-sonnet-5-5",
          name: "Sonnet 5.5",
          efforts: ["low"],
          modes: [
            {
              id: "default",
              name: "Default",
              description: "Native Claude tool approval",
            },
          ],
          fast: false,
        },
      ],
    });
  });
  let settings = {
    revision: 1,
    selection: null as unknown,
    effective: null as unknown,
  };
  let captured: unknown;
  await page.route(
    `**/api/projects/${project}/coordinator-settings`,
    (route) => {
      if (route.request().method() === "PUT") {
        captured = route.request().postDataJSON();
        const choice = route.request().postDataJSON().selection;
        settings = {
          revision: 2,
          selection: choice,
          effective: {
            choice,
            source: "project",
            default_revision: 2,
            override_revision: null,
            registration: null,
          },
        };
      }
      return route.fulfill({ json: settings });
    },
  );
  await page.goto(`/projects/${project}`);
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
    exact: true,
  });
  await expect(picker).toBeVisible();
  let commandDiscoveries = 0;
  await page.route("**/coordinator/commands/discover*", (route) => {
    commandDiscoveries++;
    return route.fulfill({ json: [] });
  });
  const input = page.getByRole("textbox", {
    name: "Message coordinator",
    exact: true,
  });
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  await input.click();
  await input.fill("/");
  await page
    .getByRole("option", { name: "Load commands", exact: true })
    .click();
  await expect(
    page.getByText("Save a harness and its supported model settings first.", {
      exact: true,
    }),
  ).toBeVisible();
  expect(commandDiscoveries).toBe(0);
  await page
    .getByRole("combobox", { name: "Message coordinator", exact: true })
    .press("Escape");
  await input.fill("");
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await expect(
    picker.getByRole("combobox", { name: "Harness", exact: true }),
  ).toContainText("Claude Code");
  await expect.poll(() => discoveries).toBe(2);
  await choose(
    picker.getByLabel("Model", { exact: true }),
    "claude-sonnet-5-5",
  );
  await choose(picker.getByLabel("Reasoning effort"), "low");
  await choose(picker.getByLabel("Access mode"), "default");
  await picker.getByRole("button", { name: "Save", exact: true }).click();
  await expect
    .poll(() => captured)
    .toEqual({
      expected_revision: 1,
      selection: {
        harness: "claude-code",
        model: "claude-sonnet-5-5",
        effort: "low",
        mode: "default",
        fast: false,
      },
    });
  await expect(
    page.getByRole("button", {
      name: "Claude Code · claude-sonnet-5-5 · low",
      exact: true,
    }),
  ).toBeVisible();
  const workers = await (
    await request.get(`/api/projects/${project}/workers`)
  ).json();
  expect(workers.selection).toBeNull();
  expect(discoveries).toBe(2);
  expect(commandDiscoveries).toBe(0);
  await page
    .getByRole("button", {
      name: "Claude Code · claude-sonnet-5-5 · low",
      exact: true,
    })
    .click();
  await page.screenshot({
    path: testInfo.outputPath("claude-choice-desktop.png"),
    animations: "disabled",
  });
  for (const name of ["Harness", "Model"]) {
    await picker.getByRole("combobox", { name, exact: true }).click();
    await page.screenshot({
      path: testInfo.outputPath(`${name.toLowerCase()}-menu.png`),
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page
    .getByRole("button", {
      name: "Claude Code · claude-sonnet-5-5 · low",
      exact: true,
    })
    .click();
  await expect(picker).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("claude-choice-mobile.png"),
    animations: "disabled",
  });
});

test("switching harnesses disables dependent controls and ignores late catalogs", async ({
  page,
  request,
}) => {
  const project = "harness-switch-race";
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "HSR",
        },
      })
    ).ok(),
  ).toBe(true);
  await page.route("**/api/harnesses", (route) =>
    route.fulfill({ json: [hostStatus("codex"), hostStatus("claude-code")] }),
  );
  let releaseClaude!: () => void;
  const delayed = new Promise<void>((resolve) => {
    releaseClaude = resolve;
  });
  let requestedClaude = false;
  await page.route("**/api/worker-models*", async (route) => {
    const claude =
      new URL(route.request().url()).searchParams.get("harness") ===
      "claude-code";
    if (claude) {
      requestedClaude = true;
      await delayed;
    }
    await route
      .fulfill({
        json: [
          {
            id: claude ? "claude-model" : "codex-model",
            name: claude ? "Claude model" : "Codex model",
            efforts: ["low"],
            modes: [{ id: "default", name: "Default" }],
            fast: false,
          },
        ],
      })
      .catch(() => {});
  });
  await page.goto(`/projects/${project}/edit/workers`);
  const form = page.getByRole("region", {
    name: "Worker settings",
    exact: true,
  });
  const harness = form.getByLabel("Harness", { exact: true });
  const model = form.getByLabel("Model", { exact: true });
  await expect(model).toBeEnabled();
  await choose(model, "codex-model");
  await choose(harness, "claude-code");
  await expect.poll(() => requestedClaude).toBe(true);
  await expect(harness).toBeEnabled();
  await expect(model).toBeDisabled();
  await expect(form.getByLabel("Reasoning effort")).toBeDisabled();
  await expect(
    form.getByRole("button", { name: "Save worker settings" }),
  ).toBeDisabled();
  await choose(harness, "codex");
  await expect(model).toBeEnabled();
  await choose(model, "codex-model");
  releaseClaude();
  await expect(model).toHaveAttribute("data-value", "codex-model");
  await model.click();
  await expect(
    page.getByRole("option", { name: "Codex model", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "Claude model", exact: true }),
  ).toHaveCount(0);
});

test("configured project choices preload and unknown harness status stays loading", async ({
  page,
  request,
  context,
}) => {
  const project = "preloaded-choices";
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "PLC",
        },
      })
    ).ok(),
  ).toBe(true);
  const choice = {
    harness: "codex",
    model: "saved-model",
    effort: "low",
    mode: "default",
    fast: false,
  };
  await page.route(`**/api/projects/${project}/coordinator-settings`, (route) =>
    route.fulfill({
      json: {
        revision: 1,
        selection: choice,
        effective: {
          choice,
          source: "project",
          default_revision: 1,
          override_revision: null,
          registration: null,
        },
      },
    }),
  );
  await page.route(`**/api/projects/${project}/workers`, (route) =>
    route.fulfill({
      json: {
        project_id: project,
        revision: 1,
        selection: { ...choice, harness: "claude-code" },
        max_parallel: 1,
        enabled: false,
        problem: null,
      },
    }),
  );
  let releaseHosts!: () => void;
  const hosts = new Promise<void>((resolve) => {
    releaseHosts = resolve;
  });
  await page.route("**/api/harnesses", async (route) => {
    await hosts;
    await route.fulfill({
      json: [hostStatus("codex"), hostStatus("claude-code")],
    });
  });
  let releaseModels!: () => void;
  const models = new Promise<void>((resolve) => {
    releaseModels = resolve;
  });
  const discovered: string[] = [];
  await page.route("**/api/worker-models*", async (route) => {
    const query = new URL(route.request().url()).searchParams;
    expect(query.get("project_id")).toBe(project);
    discovered.push(query.get("harness")!);
    await models;
    await route.fulfill({
      json: [
        {
          id: "saved-model",
          name: "Saved model",
          efforts: ["low"],
          modes: [{ id: "default", name: "Default" }],
          fast: true,
        },
      ],
    });
  });
  await page.goto(`/projects/${project}`);
  const trigger = page.getByRole("button", {
    name: "Codex · saved-model · low",
    exact: true,
  });
  await trigger.click();
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
    exact: true,
  });
  const harness = picker.getByRole("combobox", {
    name: "Harness",
    exact: true,
  });
  await expect(harness).toBeDisabled();
  await expect(harness).toHaveText("Loading harnesses…");
  await expect(picker).not.toContainText("setup needed");
  await expect(
    picker.getByRole("combobox", { name: "Model", exact: true }),
  ).toBeDisabled();
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  releaseHosts();
  await expect
    .poll(() => discovered.slice().sort())
    .toEqual(["claude-code", "codex"]);
  await expect(picker).toHaveCount(0);
  releaseModels();
  await expect(
    page.getByRole("button", { name: "Fast mode", exact: true }),
  ).toBeEnabled();
  await trigger.click();
  await expect(harness).toBeEnabled();
  await expect(harness).toContainText("Codex");
  await expect(
    picker.getByRole("combobox", { name: "Model", exact: true }),
  ).toHaveText("Saved model");
  await expect(picker.getByText("Fast mode", { exact: true })).toHaveCount(0);
  expect(discovered).toHaveLength(2);
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.reload();
  await expect.poll(() => discovered.length).toBe(4);
  await expect(picker).toHaveCount(0);
  // Reconnection warms the active project's choices again without opening a picker.
  await context.setOffline(true);
  await expect(
    page.getByRole("status", { name: "Disconnected", exact: true }),
  ).toBeVisible();
  await context.setOffline(false);
  await expect.poll(() => discovered.length).toBe(6);
  await expect(picker).toHaveCount(0);
});
