import { state } from "./support";
import { dialogViews } from "./dialog-evidence";
import { fixtureProject, projectWorkspace, choose, test } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import type { components } from "../src/api-schema";

type Turn = components["schemas"]["CoordinatorTurn"];

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
        await fixtureProject(request, {
          data: {
            path: directory,
            name: id === "chat-browser" ? "Chat Browser" : "Chat Other",
          },
        })
      ).ok(),
    ).toBeTruthy();
  }
  expect(
    (
      await request.post(
        `/api/workspaces/${await projectWorkspace(request, "chat-browser")}/tasks`,
        {
          data: { title: "Plan the chat experience" },
        },
      )
    ).ok(),
  ).toBeTruthy();
  const workspace = await projectWorkspace(request, "chat-browser");
  const historyPath = `/api/workspaces/${workspace}/coordinator`;
  const first = (await request.post(historyPath)).json();
  const conversation = await first;
  const turns: Turn[] = [];
  let active: Turn | null = null;
  let sends = 0;
  let recovery: string | null = null;
  let permission: components["schemas"]["PermissionRecord"] | null = null;
  await page.route(`**/api/workspaces/${workspace}/permissions?*`, (route) => {
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
    `**/api/workspaces/${workspace}/permissions/setup/answer`,
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
      workspace_id: workspace,
      conversation_id: conversation.id,
      created_at: new Date().toISOString(),
      status: "running",
      native_started: true,
      notice: "",
      settings: {
        choice: {
          harness: "codex",
          model: "test-model-with-a-very-long-model-identifier",
          effort: "low",
          mode: "read-only",
        },
        source: "workspace",
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
  await page.route(`**/api/workspaces/${workspace}/models*`, (route) =>
    route.fulfill({
      json: [
        {
          id: "test-model-with-a-very-long-model-identifier",
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
    `**/api/workspaces/${workspace}/coordinator-settings`,
    (route) =>
      route.fulfill({
        json: {
          revision: 1,
          selection: {
            harness: "codex",
            model: "test-model-with-a-very-long-model-identifier",
            effort: "low",
            mode: "read-only",
          },
          effective: {
            choice: {
              harness: "codex",
              model: "test-model-with-a-very-long-model-identifier",
              effort: "low",
              mode: "read-only",
            },
            source: "workspace",
            default_revision: 1,
            override_revision: null,
          },
        },
      }),
  );
  let discoveries = 0;
  await page.route(
    `**/api/workspaces/${workspace}/coordinator/commands/discover*`,
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "chat-browser")}/tasks`,
  );
  await expect(
    page.getByRole("region", { name: "Coordinator conversation", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: /T-1.*Plan the chat experience/ }),
  ).toBeVisible();
  const input = page.getByRole("textbox", { name: "Message coordinator" });
  expect(discoveries).toBe(0);
  await input.fill("/");
  await expect(
    page.getByRole("option", { name: "Load commands", exact: true }),
  ).toHaveCount(0);
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
    .getByRole("button", {
      name: "Codex · test-model-with-a-very-long-model-identifier · low",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Coordinator model settings" }),
  ).toBeVisible();
  await expect(page.getByLabel("Harness", { exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await input.click({ position: { x: 8, y: 8 } });
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
  await page.getByLabel("Workspace", { exact: true }).click();
  await page.getByRole("option", { name: "Chat Other", exact: true }).click();
  await page.getByLabel("Workspace", { exact: true }).click();
  await page.getByRole("option", { name: "Chat Browser", exact: true }).click();
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
  await page
    .getByRole("separator", { name: "Resize coordinator and work" })
    .press("Home");
  async function actionFits(name: string) {
    return page
      .getByRole("button", { name, exact: true })
      .evaluate((button) => {
        const frame = button
          .closest(".composer-input")!
          .getBoundingClientRect();
        const box = button.getBoundingClientRect();
        return (
          box.right <= frame.right - 4 &&
          box.left >= frame.left &&
          box.width >= 32
        );
      });
  }
  expect(await actionFits("Send")).toBe(true);
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeEnabled();
  await input.press("Enter");
  await expect(page.getByText("Let’s plan the work.")).toBeVisible();
  expect(await actionFits("Stop")).toBe(true);
  const activity = page.getByRole("status", {
    name: "Coordinator activity",
    exact: true,
  });
  await expect(activity).toHaveText("Working…");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(activity.locator("svg")).toHaveCSS("animation-name", "none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.screenshot({
    path: testInfo.outputPath("coordinator-working-minimum.png"),
  });
  await page
    .getByRole("button", {
      name: "Codex · test-model-with-a-very-long-model-identifier · low",
      exact: true,
    })
    .click();
  const activeSettings = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await expect(
    activeSettings.getByLabel("Harness", { exact: true }),
  ).toBeDisabled();
  await expect(activeSettings).toContainText("Stop or finish this message");
  await expect(activeSettings.getByLabel("Harness", { exact: true })).toHaveCSS(
    "margin-top",
    "6px",
  );
  await expect(activeSettings.getByLabel("Model", { exact: true })).toHaveCSS(
    "margin-top",
    "6px",
  );
  await expect(page.getByText(/^Coordinator · /)).toHaveCount(0);
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
    review_attempt_id: null,
    workspace_id: workspace,
    role: "coordinator",
    task_id: null,
    run_id: null,
    conversation_id: conversation.id,
    binding: "test-binding",
    turn_id: active!.id,
    tool_id: "setup-tool",
    title:
      "mcp__flowfield_9c2faa714516495184bec40458adaf5f__" +
      "get_workers".repeat(12),
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
      await request.post(
        `/api/workspaces/${await projectWorkspace(request, "chat-browser")}/tasks`,
        {
          data: { title: "Record the follow-up" },
        },
      )
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
  await expect(activity).toHaveText("Waiting for permission");
  await expect(activity.locator("svg")).toHaveCount(0);
  expect(
    await longChoice.evaluate(
      (button) =>
        button.scrollWidth <= button.clientWidth && button.clientHeight <= 36,
    ),
  ).toBe(true);
  expect(
    await page
      .locator(".coordinator-scroll")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-permission.png"),
  });
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await expect(activity).toHaveText("Working…");
  await expect(
    page.getByText("Tool permission history", { exact: true }),
  ).toHaveCount(0);
  expect(turns[0].text).toContain(
    `[screen.png](/api/workspaces/${workspace}/attachments/`,
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
    " Open [T-1](/projects/chat-browser/tasks/T-1).";
  await page.evaluate(
    ({ workspace, turn }) =>
      window.dispatchEvent(
        new CustomEvent("flowfield:activity", {
          detail: { coordinator_activity: [[workspace, turn]] },
        }),
      ),
    { workspace, turn: active!.id },
  );
  await expect(
    page
      .getByRole("region", { name: "Coordinator conversation" })
      .getByRole("link", { name: "T-1" }),
  ).toBeVisible();
  await input.fill("A draft for the next turn");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(
    page.getByText(
      "Stopped. Saved task changes remain; workers continue independently.",
    ),
  ).toBeVisible();
  await expect(input).toHaveValue("A draft for the next turn");
  await expect(activity).toHaveCount(0);
  await page
    .getByRole("button", {
      name: "Codex · test-model-with-a-very-long-model-identifier · low",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("Harness", { exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Tool", { exact: true })).toBeHidden();
  await expect(
    page.getByRole("link", { name: "T-1", exact: true }).first(),
  ).toBeVisible();
  await page.locator(".coordinator-activity-summary > summary").click();
  await expect(page.getByLabel("Tool", { exact: true })).toBeVisible();
  const timeline = page.locator(
    ".coordinator-message:not(.coordinator-human) [data-kind]",
  );
  await expect(timeline).toHaveText([
    "I’ll read the project first.",
    "Read project · completed\n/project/README.md",
    "Let’s plan the work. Open T-1.",
  ]);
  await expect(timeline.nth(1)).toHaveCSS(
    "color",
    await timeline.nth(0).evaluate((el) => getComputedStyle(el).color),
  );
  await expect(
    page.getByRole("button", { name: "Collapse projects" }),
  ).toHaveCount(0);
  for (const control of [
    page.getByRole("link", { name: "Home", exact: true }),
    page.getByRole("link", { name: "Flowfield settings", exact: true }),
    page.getByLabel("Workspace", { exact: true }),
    page.locator(".connection"),
  ])
    await expect(control).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("workspace-sidebar.png") });
  await expect(
    page.getByRole("button", { name: "Expand projects" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Coordinator · stopped", { exact: true }),
  ).toHaveCount(0);
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
    expect(route.request().url()).toContain(
      `/turns/${turns[0].id}/reset-session`,
    );
    if (resets === 1)
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "fixture_reset_failed",
            message: "Session recovery could not be completed.",
          },
        },
      });
    recovery = null;
    await route.fulfill({ status: 204 });
  });
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByText("The saved agent session could not be resumed.", {
      exact: true,
    }),
  ).toBeVisible();
  const reset = page.getByRole("button", {
    name: "Start new session",
    exact: true,
  });
  await expect(reset).toHaveCount(1);
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(reset).toBeVisible();
  expect(sends).toBe(1);
  expect(resets).toBe(0);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      await expect(reset).toBeVisible();
      await reset.focus();
      await expect(reset).toBeFocused();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: testInfo.outputPath(
          `coordinator-recovery-${width}-${colorScheme}.png`,
        ),
        animations: "disabled",
      });
    }
  }
  expect(resets).toBe(0);
  await dialogViews(page, testInfo, "coordinator-session-recovery");
  await reset.click();
  const resetError = page.locator("[data-sonner-toast][data-type=error]");
  await expect(resetError).toHaveCount(1);
  await expect(resetError).toContainText(
    "Could not update coordinator session",
  );
  await expect(reset).toBeEnabled();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await resetError.getByRole("button", { name: "Close toast" }).click();
  await reset.click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(reset).toHaveCount(0);
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toHaveCount(0);
  expect(resets).toBe(2);
  await expect(
    page.locator("[data-sonner-toast][data-type=success]"),
  ).toHaveCount(0);
  expect(sends).toBe(1);
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(reset).toHaveCount(0);
  expect(resets).toBe(2);
  expect(sends).toBe(1);
  active = turns[0];
  active.status = "uncertain";
  active.notice = "Native cleanup needs confirmation.";
  let confirmations = 0;
  await page.route(`**${historyPath}/turns/*/confirm-stopped`, (route) => {
    confirmations++;
    expect(route.request().url()).toContain(
      `/turns/${turns[0].id}/confirm-stopped`,
    );
    if (confirmations === 1)
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "fixture_stop_failed",
            message: "Cleanup is still uncertain.",
          },
        },
      });
    active!.status = "interrupted";
    active = null;
    return route.fulfill({ status: 204 });
  });
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await page
    .getByRole("button", {
      name: "Codex · test-model-with-a-very-long-model-identifier · low",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("Harness", { exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Confirm coordinator stopped", exact: true })
    .click();
  const stopDialog = page.getByRole("alertdialog");
  await expect(
    stopDialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await dialogViews(page, testInfo, "coordinator-confirm-stopped");
  await page.keyboard.press("Escape");
  expect(confirmations).toBe(0);
  await expect(stopDialog).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: "Confirm coordinator stopped",
      exact: true,
    }),
  ).toBeFocused();
  await page
    .getByRole("button", { name: "Confirm coordinator stopped", exact: true })
    .click();
  await stopDialog
    .getByRole("button", { name: "Confirm coordinator stopped", exact: true })
    .click();
  await expect(resetError).toHaveCount(1);
  await expect(resetError).toContainText("Cleanup is still uncertain.");
  await resetError.getByRole("button", { name: "Close toast" }).click();
  await page
    .getByRole("button", { name: "Confirm coordinator stopped", exact: true })
    .click();
  await stopDialog
    .getByRole("button", { name: "Confirm coordinator stopped", exact: true })
    .click();
  await expect.poll(() => confirmations).toBe(2);
  await page
    .getByRole("button", {
      name: "Codex · test-model-with-a-very-long-model-identifier · low",
      exact: true,
    })
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
  await fixtureProject(request, {
    data: { path: directory, name: "Chat Settings" },
  });
  const workspace = await projectWorkspace(request, "chat-settings");
  let release!: () => void;
  const discovery = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/workspaces/${workspace}/models*`, async (route) => {
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
    `**/api/workspaces/${workspace}/coordinator-settings`,
    async (route) => {
      if (route.request().method() === "PUT") {
        const change = route.request().postDataJSON();
        expect(change.selection.mode).toBe("agent");
        settings = {
          revision: settings.revision + 1,
          selection: change.selection,
          effective: {
            choice: change.selection,
            source: "workspace",
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
    `**/api/workspaces/${workspace}/coordinator/messages`,
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "chat-settings")}/tasks`,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("dialog", { name: "Coordinator model settings" }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Coordinator model settings" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await choose(page.getByLabel("Harness", { exact: true }), "codex");
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
  await expect(effort).toBeDisabled();
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(effort).toHaveText("Loading efforts…");
  await input.click({ position: { x: 8, y: 8 } });
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
  await choose(page.getByLabel("Harness", { exact: true }), "codex");
  await expect(model).toBeEnabled();
  await expect(model).toHaveText("Choose a model");
  await expect(effort).toBeDisabled();
  await expect(effort).toHaveText("Select a model first");
  await choose(model, "second");
  await expect(effort).toBeEnabled();
  await choose(effort, "high");
  await choose(page.getByRole("combobox", { name: "Access mode" }), "agent");
  await expect(send).toBeDisabled();
  await input.click({ position: { x: 8, y: 8 } });
  await expect(model).not.toBeVisible();
  await expect(input).toBeEditable();
  await page
    .getByRole("button", { name: "Model settings", exact: true })
    .click();
  await choose(page.getByLabel("Harness", { exact: true }), "codex");
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
  // Moving straight between adjacent triggers must open each tooltip without
  // a second hover, while the tooltip itself remains hoverable.
  const contextRing = page.locator(".composer-toolbar .context-ring");
  await expect(contextRing).toBeVisible();
  for (let i = 0; i < 3; i++) {
    await contextRing.hover();
    await expect(page.getByRole("tooltip")).toContainText("context usage");
    await fastButton.hover();
    await expect(page.getByRole("tooltip")).toContainText("Fast mode off");
  }
  await page.getByRole("tooltip").hover();
  await expect(page.getByRole("tooltip")).toContainText("Fast mode off");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
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
  await expect(
    page.getByRole("button", { name: "Reset", exact: true }),
  ).toHaveCount(0);
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
  await input.click({ position: { x: 8, y: 8 } });
  await send.click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Model is unavailable");
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
  await fixtureProject(request, {
    data: { path: directory },
  });
  const workspace = await projectWorkspace(request, "long-chat-project");
  await page.route(`**/api/workspaces/${workspace}/models*`, (route) =>
    route.fulfill({ json: [] }),
  );
  const historyPath = `/api/workspaces/${workspace}/coordinator`;
  let count = 40;
  const turn = (number: number): Turn => ({
    guidance_revision: null,
    view_context: null,
    task_context: null,
    id: `history-message-${number}`,
    number,
    workspace_id: workspace,
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
      source: "workspace",
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
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "long-chat-project")}/tasks`,
  );
  await page.getByRole("button", { name: "Load earlier messages" }).click();
  const messages = page.locator(".coordinator-turn");
  await expect(messages).toHaveCount(40);
  count = 41;
  await request.post(
    `/api/workspaces/${await projectWorkspace(request, "long-chat-project")}/tasks`,
    {
      data: { title: "Refresh project state" },
    },
  );
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
  await request.post(
    `/api/workspaces/${await projectWorkspace(request, "long-chat-project")}/tasks`,
    {
      data: { title: "Reconnect with newer work" },
    },
  );
  await expect(messages).toHaveCount(100);
  const scroll = page.locator(".coordinator-scroll");
  await scroll.hover();
  await page.mouse.wheel(0, -400);
  await expect(scroll).toHaveAttribute("data-scrolling", "true");
  await expect(scroll).toHaveAttribute("data-overflow-top", "true");
  await expect(scroll).not.toHaveAttribute("data-scrolling", "true");
  await scroll.evaluate((element) => element.scrollTo({ top: 0 }));
  await expect(scroll).toHaveAttribute("data-overflow-top", "false");
  await expect(
    page.getByText("Planning exchange 60", { exact: true }),
  ).toBeAttached();
});

test("automatic task context preserves one coordinator and immutable retry receipts", async ({
  page,
  request,
}, testInfo) => {
  const project = "focused-chat";
  mkdirSync(join(state, project), { recursive: true });
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Focused chat",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  await request.post(`/api/workspaces/${workspace.id}/projects`, {
    data: { path: join(state, project) },
  });
  const tasks: { id: string; key: string; revision: number }[] = [];
  for (const title of ["Define the search", "Review the index"]) {
    tasks.push(
      await (
        await request.post(
          `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
          {
            data: {
              title,
              body: "Keep the interaction simple.",
            },
          },
        )
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
  await page.route(`**/api/workspaces/${workspace.id}/models*`, (route) =>
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
  await page.route(
    `**/api/workspaces/${workspace.id}/coordinator-settings`,
    (route) =>
      route.fulfill({
        json: {
          revision: 1,
          selection: choice,
          effective: {
            choice,
            source: "workspace",
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
  await page.route(
    `**/api/workspaces/${workspace.id}/coordinator{,?*}`,
    (route) =>
      route.fulfill({
        json: { items: turns, active: null, next_before: null, context: null },
      }),
  );
  await page.route(
    `**/api/workspaces/${workspace.id}/coordinator/messages`,
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
        activity: {
          items: [
            {
              key: "reply",
              kind: "agent",
              text: `Discuss [T-1](/workspaces/${workspace.id}/tasks/T-1).`,
              omitted: false,
            },
          ],
          omitted: false,
        },
      });
      return route.fulfill({ json: turns.at(-1) });
    },
  );
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/tasks`,
  );
  const input = page.getByLabel("Message coordinator", { exact: true });
  await input.fill("Explain this scope");
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /T-1.*Define the search/ })
    .click();
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
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toBeVisible();
  await request.put(
    `/api/workspaces/${await projectWorkspace(request, project)}/tasks/${tasks[0].id}`,
    {
      data: {
        expected_revision: tasks[0].revision,
        body: "Updated scope after the uncertain response.",
      },
    },
  );
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
  const humanMessage = page.locator(".coordinator-human").first();
  await expect(humanMessage).toHaveText("Explain this scope");
  await expect(humanMessage.locator("time")).toHaveCount(0);
  await expect(humanMessage.getByText(/Context:|You ·/)).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Coordinator conversation" }),
  ).toContainText("T-1");
  await page.locator(".entity-pane .entity-pane-body").evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("task-beside-coordinator.png"),
  });
  await detail.getByRole("button", { name: "Back" }).click();
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /T-2.*Review the index/ })
    .click();
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Coordinator conversation" }),
  ).toContainText("T-1");
  await detail.getByRole("button", { name: "Back" }).click();
  await input.fill("Discuss the whole project");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(2);
  expect(messages[1].task_context).toBeNull();
  await page
    .locator(".workspace-resource-content")
    .getByRole("link", { name: /T-2.*Review the index/ })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(detail).toBeVisible();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(input).toBeVisible();
  await page
    .getByRole("region", { name: "Coordinator conversation" })
    .getByRole("link", { name: "T-1", exact: true })
    .first()
    .click();
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("Define the search");
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.evaluate(() => {
    localStorage.setItem("flowfield.theme", "dark");
    window.dispatchEvent(
      new StorageEvent("storage", { key: "flowfield.theme" }),
    );
  });
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.screenshot({
    path: testInfo.outputPath("task-context-mobile.png"),
    animations: "disabled",
  });
});

test("coordinator final replies render directly despite abridged tool activity", async ({
  page,
  request,
}, testInfo) => {
  const project = "chat-full-reply";
  mkdirSync(join(state, project), { recursive: true });
  expect(
    (
      await fixtureProject(request, {
        data: { path: join(state, project) },
      })
    ).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, project);
  const conversation = await (
    await request.post(`/api/workspaces/${workspace}/coordinator`)
  ).json();
  const now = new Date().toISOString();
  const turn = {
    id: "long-reply-turn",
    number: 1,
    workspace_id: workspace,
    conversation_id: conversation.id,
    text: "Explain the decision",
    task_context: null,
    created_at: now,
    status: "completed",
    native_started: true,
    session: "new",
    notice: "",
    applied: null,
    launch: null,
    settings: {
      choice: { harness: "codex", model: "test-model", effort: "low" },
      source: "workspace",
      default_revision: 1,
    },
    activity: {
      revision: 7,
      supported: true,
      active: false,
      changed: true,
      omitted: false,
      context: null,
      items: [
        {
          key: "reply",
          kind: "agent",
          text: "Opening. Saved middle of the explanation. Final decision: keep the public API.",
          omitted: false,
          preview: "",
          abridged: true,
        },
      ],
    },
  };
  await page.route(`**/api/workspaces/${workspace}/coordinator{,?*}`, (route) =>
    route.fulfill({
      json: {
        conversation,
        items: [turn],
        active: null,
        next_before: null,
        context: null,
        session_recovery_turn_id: null,
      },
    }),
  );
  const replyRequests: string[] = [];
  const actionOutput = Array.from(
    { length: 30 },
    (_, i) => `Found task ${i + 1}`,
  ).join("\n");
  turn.activity.items.unshift({
    key: "inspect",
    kind: "tool",
    text: "",
    omitted: false,
    preview: "",
    abridged: false,
    ...{
      action: {
        title: "Flowfield · Get board · completed",
        details: 'Arguments: {"project_id":"demo"}',
        output: actionOutput,
        output_preview: "Found task 1\nFound task 2…",
      },
    },
  });
  turn.activity.items.splice(
    1,
    0,
    ...[
      {
        key: "command",
        kind: "command",
        action: {
          title: "pnpm test · failed (exit 1)",
          details: "Directory: /project\nExit code: 1",
          output: "Expected 200, received 500",
          output_preview: "Expected 200, received 500",
        },
      },
      {
        key: "read",
        kind: "tool",
        action: {
          title: "Read file · src/app.ts",
          details: 'Arguments: {"file_path":"src/app.ts","offset":10}',
          output: "export const status = 200;",
          output_preview: "export const status = 200;",
        },
      },
      {
        key: "edit",
        kind: "tool",
        action: {
          title: "Edit files · completed",
          details:
            "diff --git a/app.ts b/app.ts\n--- a/app.ts\n+++ b/app.ts\n@@ -1 +1 @@\n-const status = 500;\n+const status = 200;\n",
          output: "",
          output_preview: "",
        },
      },
      {
        key: "unknown",
        kind: "tool",
        action: {
          title: "External tool · completed",
          details: 'Arguments: {"custom_setting":true}',
          output: '{"ok":true,"count":2}',
          output_preview: '{"ok":true}',
        },
      },
      {
        key: "taskRecord",
        kind: "tool",
        action: {
          title: "Flowfield · Get task · completed",
          details: 'Arguments: {"task_id":"T-1"}',
          output:
            '{"id":"internal-task-id","title":"Deliver CSV export","body":"Download the complete inventory.","acceptance_criteria":"Empty inventories produce a valid CSV."}',
          output_preview: "Deliver CSV export",
        },
      },
    ].map((item) => ({
      ...item,
      text: "",
      preview: "",
      omitted: false,
      abridged: false,
    })),
  );
  page.on("request", (request) => {
    if (request.url().includes("/context/") && request.url().includes("/text"))
      replyRequests.push(request.url());
  });
  turn.activity.omitted = true;
  // Transport completion after the prose must not hide the final answer in Activity.
  turn.activity.items.push({
    key: "finished",
    kind: "status",
    text: "Turn completed",
    omitted: false,
    preview: "",
    abridged: false,
  });
  turn.activity.items.unshift({
    key: "thinking",
    kind: "agent",
    text: "I’ll check the current task and queue state.",
    omitted: false,
    preview: "",
    abridged: false,
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/tasks`,
  );
  await expect(
    page.getByText(/Opening. Saved middle of the explanation. Final decision:/),
  ).toBeVisible();
  await page.locator(".coordinator-activity-summary > summary").click();
  await expect(page.locator(".activity-prose")).toContainText(
    "I’ll check the current task and queue state.",
  );
  await expect(page.locator(".activity-prose")).not.toContainText(
    "Final decision: keep the public API.",
  );
  const action = page
    .locator('.run-activity-entry[data-kind="tool"]')
    .filter({ hasText: "Flowfield · Get board" });
  await action
    .getByText("Flowfield · Get board · completed", { exact: true })
    .click();
  await expect(action.locator("summary .disclosure-chevron")).toHaveCount(0);
  await expect(action.locator("dt")).toHaveText(["Project"]);
  await expect(action.locator("dd")).toHaveText("demo");
  await expect(action.locator(".activity-console")).toContainText(
    "Found task 30",
  );
  await expect(action.locator("summary")).toHaveCount(1);
  const eventSummary = action.locator("summary");
  await eventSummary.click();
  const readingTop = (await eventSummary.boundingBox())!.y;
  await eventSummary.press("Enter");
  await expect
    .poll(async () =>
      Math.abs((await eventSummary.boundingBox())!.y - readingTop),
    )
    .toBeLessThan(2);
  await expect(action.locator(".activity-console")).toBeVisible();
  await action.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("action-expanded.png") });
  await page.setViewportSize({ width: 430, height: 920 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await action.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("action-narrow-dark.png"),
  });
  const command = page.locator('.run-activity-entry[data-kind="command"]');
  await command.locator("summary").click();
  await expect(command).toContainText("Expected 200, received 500");
  await expect(command).toContainText("Exit code: 1");
  const read = page
    .locator(".run-activity-entry")
    .filter({ hasText: "Read file · src/app.ts" });
  await read.locator("summary").click();
  await expect(read.locator("dt")).toHaveText(["File", "Offset"]);
  const edit = page
    .locator(".run-activity-entry")
    .filter({ hasText: "Edit files · completed" });
  await edit.locator("summary").first().click();
  await expect(edit.locator(".diff-code-insert")).toContainText(
    "const status = 200;",
  );
  await expect(edit).not.toContainText("No output was recorded");
  const unknown = page
    .locator(".run-activity-entry")
    .filter({ hasText: "External tool · completed" });
  await unknown.locator("summary").click();
  await expect(unknown.locator("dt")).toHaveText("Custom setting");
  await expect(unknown.locator(".activity-console")).toContainText(
    '"count": 2',
  );
  const taskRecord = page
    .locator(".run-activity-entry")
    .filter({ hasText: "Flowfield · Get task · completed" });
  await taskRecord.locator("summary").first().click();
  await expect(taskRecord.locator(".activity-record")).toContainText(
    "Deliver CSV export",
  );
  await expect(taskRecord.locator(".activity-record")).toContainText(
    "Empty inventories produce a valid CSV.",
  );
  await expect(taskRecord.getByText(/internal-task-id/)).not.toBeVisible();
  await taskRecord.getByText("Saved response", { exact: true }).click();
  await expect(taskRecord.getByText(/internal-task-id/)).toBeVisible();
  await edit.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("action-kinds-narrow-dark.png"),
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.getByText("Read full reply", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByText("Earlier activity is hidden.", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(/Final decision: keep the public API/),
  ).toBeVisible();
  expect(replyRequests).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("coordinator-full-reply.png"),
  });
});

test("workspace board loading preserves the selected mobile coordinator", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const directory = join(state, "chat-alias");
  mkdirSync(directory, { recursive: true });
  await fixtureProject(request, {
    data: { path: directory, name: "Chat Alias" },
  });
  const workspace = await projectWorkspace(request, "chat-alias");
  let release!: () => void;
  const board = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/workspaces/${workspace}/view/board*`,
    async (route) => {
      await board;
      await route.continue();
    },
  );
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "chat-alias")}/tasks`,
  );
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  release();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${workspace}/tasks$`));
  await expect(
    page.getByRole("tab", { name: "Coordinator", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page
    .getByRole("textbox", { name: "Message coordinator", exact: true })
    .fill("Keep this draft");
  await page.getByRole("tab", { name: "Work", exact: true }).click();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Message coordinator", exact: true }),
  ).toHaveValue("Keep this draft");
});
