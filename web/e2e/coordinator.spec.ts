import { choose, fixtureStages } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test } from "./support";
import type { components } from "../src/api-schema";

type Turn = components["schemas"]["CoordinatorTurn"];
const state = process.env.FLOWFIELD_SMOKE_STATE!;

test.beforeEach(async ({ page }) => {
  await page.route("**/coordinator/commands/discover*", (route) =>
    route.fulfill({
      json: [
        {
          name: "compact",
          description: "Compact the conversation",
          input_hint: null,
        },
        {
          name: "status",
          description: "Show session status",
          input_hint: null,
        },
      ],
    }),
  );
});

test("coordinator streams, stops, retains history and drafts beside responsive work", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const id of ["chat-browser", "chat-other"]) {
    const directory = join(state, id);
    mkdirSync(directory, { recursive: true });
    expect(
      (
        await request.post("/api/projects/initialize", {
          data: {
            path: directory,
            name: id === "chat-browser" ? "Chat Browser" : "Chat Other",
            task_prefix: id === "chat-browser" ? "CHT" : "CHO",
          },
        })
      ).ok(),
    ).toBeTruthy();
  }
  expect(
    (
      await request.post("/api/projects/chat-browser/tasks", {
        data: { stages: fixtureStages(), title: "Plan the chat experience" },
      })
    ).ok(),
  ).toBeTruthy();
  const historyPath = "/api/projects/chat-browser/coordinator";
  const first = (await request.post(historyPath)).json();
  const conversation = await first;
  const turns: Turn[] = [];
  let active: Turn | null = null;
  let sends = 0;
  let recovery: string | null = null;
  let permission: components["schemas"]["PermissionRecord"] | null = null;
  await page.route("**/api/projects/chat-browser/permissions?*", (route) => {
    expect(new URL(route.request().url()).searchParams.get("role")).toBe(
      "coordinator",
    );
    return route.fulfill({
      json: {
        pending: permission?.status === "pending" ? [permission] : [],
        items: permission ? [permission] : [],
        next_before: null,
      },
    });
  });
  await page.route(
    "**/api/projects/chat-browser/permissions/setup/answer",
    (route) => {
      expect(route.request().postDataJSON()).toEqual({
        expected_revision: 1,
        option_id: "yes",
      });
      permission = {
        ...permission!,
        status: "answered",
        answer: "yes",
        released_at: new Date().toISOString(),
      };
      return route.fulfill({ json: permission });
    },
  );
  await page.route(`**${historyPath}{,?*}`, (route) =>
    route.fulfill({
      json: {
        conversation,
        items: turns,
        active,
        next_before: null,
        session_recovery_turn_id: recovery,
        context:
          [...turns].reverse().find((turn) => turn.activity.context)?.activity
            .context ?? null,
      },
    }),
  );
  await page.route(`**${historyPath}/messages`, async (route) => {
    sends++;
    const message = route.request().postDataJSON();
    const turn: Turn = {
      ...message,
      number: sends,
      project_id: "chat-browser",
      conversation_id: conversation.id,
      created_at: new Date().toISOString(),
      status: "running",
      native_started: true,
      notice: "",
      settings: {
        choice: {
          harness: "codex",
          model: "test-model",
          effort: "low",
          mode: "read-only",
        },
        source: "project",
        default_revision: 1,
        override_revision: 1,
      },
      applied: null,
      activity: {
        revision: 1,
        supported: true,
        active: true,
        changed: true,
        omitted: false,
        context: { used: 12000, size: 100000 },
        items: [
          {
            key: "opening",
            kind: "agent",
            text: "I’ll read the project first.",
            preview: "",
            omitted: false,
            abridged: false,
          },
          {
            key: "tool",
            kind: "tool",
            text: "Read project · completed\n/project/README.md",
            preview: "Read project · completed\n/project/README.md",
            omitted: false,
            abridged: false,
          },
          {
            key: "reply",
            kind: "agent",
            text: "Let’s plan the work.",
            omitted: false,
            preview: "",
            abridged: false,
          },
        ],
        usage: {
          input_tokens: null,
          output_tokens: null,
          cached_input_tokens: null,
          reasoning_output_tokens: null,
          total_tokens: null,
        },
      },
    };
    turns.push(turn);
    active = turn;
    await route.fulfill({ status: 202, json: turn });
  });
  await page.route(`**${historyPath}/turns/*/stop`, async (route) => {
    if (active) {
      active.status = "stopped";
      active.notice =
        "Stopped. Saved task changes remain; workers continue independently.";
    }
    const saved = active;
    active = null;
    await route.fulfill({ json: saved });
  });
  await page.route("**/api/worker-models*", (route) =>
    route.fulfill({
      json: [
        {
          id: "test-model",
          name: "Test",
          efforts: ["low"],
          modes: [
            {
              id: "read-only",
              name: "Read only",
              description: "Read project files",
            },
            {
              id: "agent",
              name: "Auto review",
              description: "Native automatic review",
            },
          ],
        },
      ],
    }),
  );
  await page.route(
    "**/api/projects/chat-browser/coordinator-settings",
    (route) =>
      route.fulfill({
        json: {
          revision: 1,
          selection: {
            harness: "codex",
            model: "test-model",
            effort: "low",
            mode: "read-only",
          },
          effective: {
            choice: {
              harness: "codex",
              model: "test-model",
              effort: "low",
              mode: "read-only",
            },
            source: "project",
            default_revision: 1,
            override_revision: null,
          },
        },
      }),
  );
  let discoveries = 0;
  await page.route(
    "**/api/projects/chat-browser/coordinator/commands/discover*",
    async (route) => {
      discoveries++;
      await route.fulfill({
        json: [
          {
            name: "compact",
            description: "Compact the conversation",
            input_hint: null,
          },
          {
            name: "status",
            description: "Show session status",
            input_hint: null,
          },
        ],
      });
    },
  );
  await page.goto("/projects/chat-browser");
  await expect(
    page.getByRole("heading", { name: "Coordinator" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /CHT-1.*Plan the chat experience/ }),
  ).toBeVisible();
  const input = page.getByRole("textbox", { name: "Message coordinator" });
  expect(discoveries).toBe(0);
  await input.fill("/");
  await page
    .getByRole("option", { name: "Load commands", exact: true })
    .click();
  await expect.poll(() => discoveries).toBe(1);
  await expect(page.getByRole("option", { name: /\/compact/ })).toBeVisible();
  await expect(page.locator("[cmdk-item]")).toHaveCount(2);
  await expect(page.locator("[cmdk-item] svg")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-commands.png"),
  });
  await page.getByRole("option", { name: /\/compact/ }).click();
  await expect(input).toHaveValue("/compact");
  expect(sends).toBe(0);
  await input.fill("/");
  await expect(page.getByRole("option", { name: /\/compact/ })).toBeVisible();
  expect(discoveries).toBe(1);
  await page.getByRole("combobox", { name: "Message coordinator" }).fill("");
  await page
    .getByRole("button", { name: "Codex · test-model · low", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Coordinator model settings" }),
  ).toBeVisible();
  await expect(page.getByLabel("Harness", { exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await input.click();
  await input.fill("/unknown");
  await page
    .getByRole("combobox", { name: "Message coordinator" })
    .press("Escape");
  await expect(input).toHaveValue("/unknown");
  await page.getByLabel("Attach files", { exact: true }).setInputFiles([
    {
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Keep the public API."),
    },
    {
      name: "screen.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=",
        "base64",
      ),
    },
  ]);
  await expect(
    page.getByRole("button", { name: "Remove screen.png" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeEnabled();
  await input.fill("Keep my project draft");
  await page.getByRole("link", { name: "Chat Other", exact: true }).click();
  await page.getByRole("link", { name: "Chat Browser", exact: true }).click();
  await expect(input).toHaveValue("Keep my project draft");
  await expect(
    page.getByRole("button", { name: "Remove notes.txt" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("composer-attachments.png"),
  });
  await input.press("End");
  await input.press("Meta+Enter");
  await expect(input).toHaveValue("Keep my project draft\n");
  await input.press("Control+Enter");
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("Keep my project draft\n\n\n");
  expect(sends).toBe(0);
  await input.press("Enter");
  await expect(page.getByText("Let’s plan the work.")).toBeVisible();
  await page
    .getByRole("button", { name: "Codex · test-model · low", exact: true })
    .click();
  const activeSettings = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await expect(
    activeSettings.getByLabel("Harness", { exact: true }),
  ).toBeDisabled();
  await expect(activeSettings).toContainText("Stop or finish this message");
  await expect(
    activeSettings.getByRole("button", { name: "Refresh models", exact: true }),
  ).toBeEnabled();
  await page.keyboard.press("Escape");
  const now = new Date().toISOString();
  const longPermissionLabel =
    "Yes, and don't ask again for commands that start with `node -e '" +
    'fetch("http://127.0.0.1:8902/pocket-list.js");'.repeat(8) +
    'console.log("complete-prefix")\'`';
  permission = {
    id: "setup",
    project_id: "chat-browser",
    role: "coordinator",
    task_id: null,
    run_id: null,
    conversation_id: conversation.id,
    binding: "test-binding",
    turn_id: active!.id,
    tool_id: "setup-tool",
    title: "Install project dependencies",
    details: "command: pnpm install",
    options: [
      { id: "yes", label: "Allow once", kind: "allow_once" },
      { id: "future", label: longPermissionLabel, kind: "allow_always" },
    ],
    revision: 1,
    status: "pending",
    answer: null,
    created_at: now,
    updated_at: now,
    expires_at: now,
    released_at: null,
  };
  // Native permission persistence emits a project change, not an activity-only
  // notification. Exercise the real change stream for this scripted permission.
  expect(
    (
      await request.post("/api/projects/chat-browser/tasks", {
        data: { stages: fixtureStages(), title: "Record the follow-up" },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    page.getByRole("button", { name: "Allow once", exact: true }),
  ).toBeVisible();
  const longChoice = page.getByRole("button", {
    name: longPermissionLabel,
    exact: true,
  });
  await expect(longChoice).toHaveText(longPermissionLabel);
  await longChoice.scrollIntoViewIfNeeded();
  expect(
    await longChoice.evaluate(
      (button) =>
        button.scrollWidth <= button.clientWidth && button.clientHeight > 32,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-permission.png"),
  });
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await expect(
    page.getByText("Tool permission history", { exact: true }),
  ).toBeVisible();
  expect(turns[0].text).toContain(
    "[screen.png](/api/projects/chat-browser/attachments/",
  );
  await page
    .locator(".composer-toolbar")
    .getByLabel("12% context used")
    .hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "12,000 / 100,000 tokens (12%)",
  );
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toHaveCount(0);
  active!.activity.items.find((item) => item.key === "reply")!.text +=
    " Open [CHT-1](/projects/chat-browser/tasks/CHT-1).";
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("flowfield:activity", {
        detail: { projects: ["chat-browser"] },
      }),
    ),
  );
  await expect(
    page
      .getByRole("region", { name: "Coordinator conversation" })
      .getByRole("link", { name: "CHT-1" }),
  ).toBeVisible();
  await input.fill("A draft for the next turn");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(
    page.getByText(
      "Stopped. Saved task changes remain; workers continue independently.",
    ),
  ).toBeVisible();
  await expect(input).toHaveValue("A draft for the next turn");
  await page
    .getByRole("button", { name: "Codex · test-model · low", exact: true })
    .click();
  await expect(page.getByLabel("Harness", { exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Tool", { exact: true })).toBeVisible();
  const timeline = page.locator(
    ".coordinator-message:not(.coordinator-human) > [data-kind]",
  );
  await expect(timeline).toHaveText([
    "I’ll read the project first.",
    "Read project · completed\n/project/README.md",
    "Let’s plan the work. Open CHT-1.",
  ]);
  await expect(timeline.nth(1)).not.toHaveCSS(
    "color",
    await timeline.nth(0).evaluate((el) => getComputedStyle(el).color),
  );
  await expect(
    page.getByRole("button", { name: "Collapse projects" }),
  ).toHaveCount(0);
  const rail = page.locator('[data-sidebar="sidebar"]').first();
  for (const control of [
    page.getByRole("link", { name: "Flowfield", exact: true }),
    page.getByRole("button", { name: /^Appearance:/ }),
    page.getByRole("button", { name: /^Notifications/ }),
    page.getByRole("button", { name: "Add project", exact: true }),
    page.getByRole("link", { name: "Chat Browser", exact: true }),
    page.locator(".connection"),
  ]) {
    await expect
      .poll(
        async () => {
          const bounds = await rail.boundingBox();
          const icon = await control
            .locator("svg, img, .project-badge, .live-dot")
            .first()
            .boundingBox();
          return Math.abs(
            bounds!.x + bounds!.width / 2 - icon!.x - icon!.width / 2,
          );
        },
        {
          message: `Centered ${(await control.getAttribute("aria-label")) ?? "connection"}`,
        },
      )
      .toBeLessThan(1);
  }
  await page.screenshot({ path: testInfo.outputPath("collapsed-sidebar.png") });
  const connection = page.locator(".connection");
  await expect(connection.locator(".live-dot")).toBeVisible();
  await expect
    .poll(async () =>
      connection.evaluate((el) => {
        const frame = el.getBoundingClientRect();
        const dot = el.querySelector(".live-dot")!.getBoundingClientRect();
        return Math.abs(
          (frame.left + frame.right) / 2 - (dot.left + dot.right) / 2,
        );
      }),
    )
    .toBeLessThan(1);
  await expect(
    page.getByRole("button", { name: "Expand projects" }),
  ).toHaveCount(0);
  await expect(
    page.locator(".coordinator-message").last().locator(":scope > :last-child"),
  ).toHaveText("Coordinator · stopped");
  await expect(
    page.getByText("Read project · completed", { exact: false }),
  ).toContainText("/project/README.md");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-desktop.png"),
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(input).toHaveCSS("color", "rgb(227, 231, 236)");
  await page.screenshot({
    path: testInfo.outputPath("coordinator-dark.png"),
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(input).toBeVisible();
  await page.getByRole("tab", { name: "Work", exact: true }).click();
  await expect(input).not.toBeVisible();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(input).toHaveValue("A draft for the next turn");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-mobile.png"),
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-mobile.png"),
  });
  turns.push({
    ...turns[0],
    id: "command-without-usage",
    number: 2,
    text: "/status",
    activity: { ...turns[0].activity, items: [], context: null },
  });
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(page.getByLabel("12% context used")).toBeVisible();
  await page.getByLabel("12% context used").hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "Last reported: 12,000",
  );
  await expect(
    page.getByText("Let’s plan the work.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "New conversation", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "Conversation history" }),
  ).toHaveCount(0);
  expect(sends).toBe(1);
  turns[0].status = "failed";
  turns[0].session = "unavailable";
  turns[0].activity.items = [];
  turns[0].notice = "The saved agent session could not be resumed.";
  recovery = turns[0].id;
  let resets = 0;
  await page.route(`**${historyPath}/turns/*/reset-session`, async (route) => {
    resets++;
    recovery = null;
    await route.fulfill({ status: 204 });
  });
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByText("Coordinator · failed", { exact: true }),
  ).toBeVisible();
  const reset = page.getByRole("button", {
    name: "Start new session",
    exact: true,
  });
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("without previous tool history");
    await dialog.dismiss();
  });
  await reset.click();
  expect(resets).toBe(0);
  page.once("dialog", (dialog) => dialog.accept());
  await reset.click();
  await expect(reset).toHaveCount(0);
  await expect(
    page.locator(".coordinator-composer").getByRole("alert"),
  ).toHaveCount(0);
  expect(resets).toBe(1);
  expect(sends).toBe(1);
  active = turns[0];
  active.status = "uncertain";
  active.notice = "Native cleanup needs confirmation.";
  await page.route(`**${historyPath}/turns/*/confirm-stopped`, (route) => {
    active!.status = "interrupted";
    active = null;
    return route.fulfill({ status: 204 });
  });
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await page
    .getByRole("button", { name: "Codex · test-model · low", exact: true })
    .click();
  await expect(page.getByLabel("Harness", { exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("button", { name: "Confirm coordinator stopped", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Codex · test-model · low", exact: true })
    .click();
  await expect(page.getByLabel("Harness", { exact: true })).toBeEnabled();
});

test("single coordinator requires a saved model, labels loading and cancels dismissed settings", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const directory = join(state, "chat-settings");
  mkdirSync(directory, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path: directory, name: "Chat Settings", task_prefix: "CST" },
  });
  let release!: () => void;
  const discovery = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/worker-models*", async (route) => {
    await discovery;
    await route.fulfill({
      json: [
        {
          id: "first",
          name: "First",
          efforts: ["low"],
          modes: [
            {
              id: "read-only",
              name: "Read only",
              description: "Read project files",
            },
            {
              id: "agent",
              name: "Auto review",
              description: "Native automatic review",
            },
          ],
        },
        {
          id: "second",
          name: "Second",
          fast: true,
          fast_description: "Faster responses, increased usage",
          efforts: ["high"],
          modes: [
            {
              id: "read-only",
              name: "Read only",
              description: "Read project files",
            },
            {
              id: "agent",
              name: "Auto review",
              description: "Native automatic review",
            },
          ],
        },
      ],
    });
  });
  type Settings = components["schemas"]["AgentSettingsView"];
  let settings: Settings = { revision: 1, selection: null, effective: null };
  await page.route(
    "**/api/projects/chat-settings/coordinator-settings",
    async (route) => {
      if (route.request().method() === "PUT") {
        const change = route.request().postDataJSON();
        expect(change.selection.mode).toBe("agent");
        settings = {
          revision: settings.revision + 1,
          selection: change.selection,
          effective: {
            choice: change.selection,
            source: "project",
            default_revision: settings.revision + 1,
            override_revision: null,
            registration: null,
          },
        };
      }
      await route.fulfill({ json: settings });
    },
  );
  let sends = 0;
  await page.route(
    "**/api/projects/chat-settings/coordinator/messages",
    (route) => {
      sends++;
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "unavailable",
            message: "Model is unavailable. Reload models.",
          },
        },
      });
    },
  );
  await page.goto("/projects/chat-settings");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("dialog", { name: "Coordinator model settings" }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Coordinator model settings" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  const model = page.getByRole("combobox", { name: "Model", exact: true });
  const effort = page.getByRole("combobox", {
    name: "Reasoning effort",
    exact: true,
  });
  const send = page.getByRole("button", { name: "Send", exact: true });
  const input = page.getByRole("textbox", { name: "Message coordinator" });
  await expect(model).toBeDisabled();
  await expect(model).toHaveText("Loading models…");
  await expect(effort).toHaveText("Loading efforts…");
  await input.click();
  await expect(model).not.toBeVisible();
  await input.fill("Plan something useful");
  await page.getByLabel("Attach files", { exact: true }).setInputFiles({
    name: "requirement.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("Keep this requirement."),
  });
  await expect(
    page.getByRole("button", { name: "Remove requirement.md" }),
  ).toBeVisible();
  await expect(send).toBeDisabled();
  await input.press("Enter");
  expect(sends).toBe(0);
  release();
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await expect(model).toBeEnabled();
  await expect(model).toHaveText("Choose a model");
  await expect(effort).toBeDisabled();
  await expect(effort).toHaveText("Select a model first");
  await choose(model, "second");
  await expect(effort).toBeEnabled();
  await choose(effort, "high");
  await choose(page.getByRole("combobox", { name: "Access mode" }), "agent");
  await expect(send).toBeDisabled();
  await input.click();
  await expect(model).not.toBeVisible();
  await expect(input).toBeEditable();
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await expect(model).toHaveAttribute("data-value", "");
  await choose(model, "second");
  await choose(effort, "high");
  await choose(page.getByRole("combobox", { name: "Access mode" }), "agent");
  await expect(model).toHaveCSS("height", "32px");
  await expect(
    page
      .getByRole("dialog", { name: "Coordinator model settings" })
      .getByRole("button", { name: "Fast mode" }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-model-settings.png"),
  });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(send).toBeEnabled();
  await expect(model).not.toBeVisible();
  const fastButton = page
    .locator(".composer-toolbar")
    .getByRole("button", { name: "Fast mode", exact: true });
  await expect(fastButton).toHaveAttribute("aria-pressed", "false");
  await fastButton.hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "Fast mode off. Faster responses, increased usage",
  );
  await fastButton.click();
  await expect(fastButton).toHaveAttribute("aria-pressed", "true");
  expect(settings.effective?.choice.fast).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(fastButton).toBeVisible();
  await page
    .getByRole("button", { name: "Codex · second · high", exact: true })
    .click();
  await choose(model, "first");
  await page.getByRole("tab", { name: "Work", exact: true }).click();
  await expect(model).not.toBeVisible();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(model).not.toBeVisible();
  await page
    .getByRole("button", { name: "Codex · second · high", exact: true })
    .click();
  await expect(model).toHaveAttribute("data-value", "second");
  await page.keyboard.press("Escape");
  await expect(send).toBeEnabled();
  const fastBounds = await fastButton.boundingBox();
  expect(fastBounds!.x + fastBounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-fast-mobile.png"),
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("button", { name: "Codex · second · high", exact: true })
    .click();
  await expect(model).toHaveAttribute("data-value", "second");
  await expect(page.getByRole("button", { name: "Use defaults" })).toHaveCount(
    0,
  );
  await choose(model, "first");
  await page.keyboard.press("Escape");
  await expect(send).toBeEnabled();
  await expect(fastButton).toBeEnabled();
  await expect(input).toBeEditable();
  await page
    .getByRole("button", { name: "Codex · second · high", exact: true })
    .click();
  await expect(model).toHaveAttribute("data-value", "second");
  await expect(effort).toHaveAttribute("data-value", "high");
  await input.click();
  await send.click();
  await expect(page.getByRole("alert")).toContainText("Model is unavailable");
  await expect(page.getByRole("button", { name: "Refresh chat" })).toHaveCount(
    0,
  );
  await expect(input).toHaveValue("Plan something useful");
  await expect(
    page.getByRole("button", { name: "Remove requirement.md" }),
  ).toBeVisible();
  expect(sends).toBe(1);
  await page.reload();
  await expect(fastButton).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Codex · second · high", exact: true }),
  ).toBeVisible();
});

test("long project chat preserves loaded history while live pages advance", async ({
  page,
  request,
}) => {
  const directory = join(state, "long-chat-project");
  mkdirSync(directory, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path: directory, task_prefix: "LCH" },
  });
  await page.route("**/api/worker-models*", (route) =>
    route.fulfill({ json: [] }),
  );
  const historyPath = "/api/projects/long-chat-project/coordinator";
  let count = 40;
  const turn = (number: number): Turn => ({
    task_context: null,
    id: `history-message-${number}`,
    number,
    project_id: "long-chat-project",
    conversation_id: "saved",
    text: `Planning exchange ${number}`,
    created_at: new Date().toISOString(),
    status: "completed",
    session: "resumed",
    native_started: true,
    notice: "",
    settings: {
      choice: {
        harness: "codex",
        model: "test",
        effort: "low",
        mode: null,
        fast: null,
      },
      source: "project",
      default_revision: 1,
      override_revision: null,
      registration: null,
    },
    applied: null,
    launch: null,
    activity: {
      revision: 1,
      supported: true,
      active: false,
      changed: true,
      omitted: false,
      items: [],
      usage: {
        input_tokens: null,
        output_tokens: null,
        total_tokens: null,
        cached_input_tokens: null,
        reasoning_output_tokens: null,
        cache_write_input_tokens: null,
        complete: false,
      },
    },
  });
  await page.route(`**${historyPath}*`, (route) => {
    const params = new URL(route.request().url()).searchParams;
    const after = Number(params.get("after"));
    if (after)
      return route.fulfill({
        json: {
          items: Array.from(
            { length: Math.min(20, count - after + 1) },
            (_, i) => turn(after + i),
          ),
          active: null,
          next_before: null,
        },
      });
    const before = Number(params.get("before")) || count + 1;
    const start = Math.max(1, before - 20);
    return route.fulfill({
      json: {
        items: Array.from({ length: before - start }, (_, i) =>
          turn(start + i),
        ),
        next_before: start > 1 ? start : null,
        active: null,
        conversation: null,
      },
    });
  });
  await page.goto("/projects/long-chat-project");
  await page.getByRole("button", { name: "Load earlier messages" }).click();
  const messages = page.locator(".coordinator-turn");
  await expect(messages).toHaveCount(40);
  count = 41;
  await request.post("/api/projects/long-chat-project/tasks", {
    data: { stages: fixtureStages(), title: "Refresh project state" },
  });
  await expect(
    page.getByText("Planning exchange 41", { exact: true }),
  ).toBeAttached();
  await expect(messages).toHaveCount(41);
  await expect(
    page.getByText("Planning exchange 21", { exact: true }),
  ).toBeAttached();
  await expect(
    page.getByRole("button", { name: "Load earlier messages" }),
  ).toHaveCount(0);
  count = 100; // More than a page arrived while this browser was disconnected.
  await request.post("/api/projects/long-chat-project/tasks", {
    data: { stages: fixtureStages(), title: "Reconnect with newer work" },
  });
  await expect(messages).toHaveCount(100);
  await expect(
    page.getByText("Planning exchange 60", { exact: true }),
  ).toBeAttached();
});

test("task focus preserves one coordinator, records context at send and restores the board", async ({
  page,
  request,
}, testInfo) => {
  const project = "focused-chat";
  mkdirSync(join(state, project), { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path: join(state, project), task_prefix: "FOC" },
  });
  const tasks: { id: string; key: string; revision: number }[] = [];
  for (const title of ["Define the search", "Review the index"]) {
    tasks.push(
      await (
        await request.post(`/api/projects/${project}/tasks`, {
          data: {
            stages: fixtureStages(),
            title,
            body: "Keep the interaction simple.",
          },
        })
      ).json(),
    );
  }
  const choice = {
    harness: "codex",
    model: "test-model",
    effort: "low",
    mode: "read-only",
    fast: false,
  };
  await page.route("**/api/worker-models*", (route) =>
    route.fulfill({
      json: [
        {
          id: "test-model",
          name: "Test",
          efforts: ["low"],
          modes: [{ id: "read-only", name: "Read only" }],
        },
      ],
    }),
  );
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
        },
      },
    }),
  );
  const turns: unknown[] = [];
  const attempts: unknown[] = [];
  const messages: {
    task_context: { task_id: string; task_revision: number } | null;
  }[] = [];
  await page.route(`**/api/projects/${project}/coordinator{,?*}`, (route) =>
    route.fulfill({
      json: { items: turns, active: null, next_before: null, context: null },
    }),
  );
  await page.route(
    `**/api/projects/${project}/coordinator/messages`,
    (route) => {
      const message = route.request().postDataJSON();
      attempts.push(message);
      if (attempts.length === 1) return route.abort("failed");
      messages.push(message);
      turns.push({
        ...message,
        task_context: message.task_context
          ? {
              ...message.task_context,
              key: tasks.find((t) => t.id === message.task_context.task_id)!
                .key,
              title: "Captured task",
            }
          : null,
        number: turns.length + 1,
        created_at: new Date().toISOString(),
        status: "completed",
        activity: { items: [], omitted: false },
      });
      return route.fulfill({ json: turns.at(-1) });
    },
  );
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/projects/${project}`);
  const input = page.getByLabel("Message coordinator", { exact: true });
  await input.fill("Explain this scope");
  await page.getByRole("link", { name: /FOC-1.*Define the search/ }).click();
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(detail).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Task details" })).toHaveCount(
    0,
  );
  await expect(input).toHaveValue("Explain this scope");
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toContainText("FOC-1");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await request.put(`/api/projects/${project}/tasks/${tasks[0].id}`, {
    data: {
      expected_revision: tasks[0].revision,
      body: "Updated scope after the uncertain response.",
    },
  });
  await expect(detail).toContainText(
    "Updated scope after the uncertain response.",
  );
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(1);
  expect(attempts[0]).toEqual(attempts[1]);
  expect(messages[0].task_context).toMatchObject({
    task_id: tasks[0].id,
    task_revision: tasks[0].revision,
  });
  await expect(
    page.getByRole("region", { name: "Coordinator conversation" }),
  ).toContainText("FOC-1");
  await page.screenshot({
    path: testInfo.outputPath("task-beside-coordinator.png"),
  });
  await detail.getByRole("button", { name: "Back to board" }).click();
  await page.getByRole("link", { name: /FOC-2.*Review the index/ }).click();
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toContainText("FOC-2");
  await expect(
    page.getByRole("region", { name: "Coordinator conversation" }),
  ).toContainText("FOC-1");
  await page.getByRole("button", { name: "Remove task context" }).click();
  await input.fill("Discuss the whole project");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(2);
  expect(messages[1].task_context).toBeNull();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(detail).toBeVisible();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(input).toBeVisible();
  await page
    .getByRole("region", { name: "Coordinator conversation" })
    .getByRole("link", { name: "FOC-1", exact: true })
    .click();
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("Define the search");
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toContainText("FOC-1");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.screenshot({
    path: testInfo.outputPath("task-context-mobile.png"),
    animations: "disabled",
  });
});
