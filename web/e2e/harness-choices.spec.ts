import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, hostStatus, existingDirectory, state } from "./support";

for (const onlyClaude of [false, true]) {
  test(`worker choices support ${onlyClaude ? "Claude alone" : "independent supported harnesses"} with explicit scoped discovery`, async ({
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
      expect(url.searchParams.get("refresh")).toBe("true");
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
    await expect(form.getByLabel("Harness", { exact: true })).toHaveValue(
      onlyClaude ? "claude-code" : "codex",
    );
    expect(discoveries).toEqual([]);
    await form
      .getByRole("button", { name: "Load models", exact: true })
      .click();
    if (!onlyClaude) {
      await form
        .getByLabel("Model", { exact: true })
        .selectOption("codex-model");
      await form.getByLabel("Reasoning effort").selectOption("low");
      await form.getByLabel("Access mode").selectOption("workspace-write");
      await form.getByLabel("Maximum parallel workers").fill("3");
      await form
        .getByLabel("Harness", { exact: true })
        .selectOption("claude-code");
      await expect(form.getByLabel("Model", { exact: true })).toHaveValue("");
      await expect(form.getByLabel("Reasoning effort")).toHaveCount(0);
      await expect(form.getByLabel("Access mode")).toHaveCount(0);
      expect(discoveries).toEqual(["codex"]);
      await form
        .getByRole("button", { name: "Load models", exact: true })
        .click();
    }
    await form
      .getByLabel("Model", { exact: true })
      .selectOption("claude-sonnet-5-5");
    await expect(form.getByLabel("Reasoning effort")).toHaveCount(0);
    await form.getByLabel("Access mode").selectOption("default");
    await expect(form.getByRole("button", { name: "Fast mode" })).toHaveCount(
      0,
    );
    let captured: unknown;
    await page.route(`**/api/projects/${project}/workers`, (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      const body = route.request().postDataJSON();
      captured = body;
      return route.fulfill({
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
    expect(discoveries).toEqual(
      onlyClaude ? ["claude-code"] : ["codex", "claude-code"],
    );
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("Claude-only coordinator saves native choices independently of worker defaults without loading while mounted", async ({
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
  await expect(picker.getByLabel("Harness", { exact: true })).toHaveValue(
    "claude-code",
  );
  expect(discoveries).toBe(0);
  await picker
    .getByRole("button", { name: "Load models", exact: true })
    .click();
  await picker
    .getByLabel("Model", { exact: true })
    .selectOption("claude-sonnet-5-5");
  await picker.getByLabel("Reasoning effort").selectOption("low");
  await picker.getByLabel("Access mode").selectOption("default");
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
  expect(discoveries).toBe(1);
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
