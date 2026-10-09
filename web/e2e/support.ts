import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import {
  test as base,
  expect,
  type Page,
  type APIRequestContext,
  type BrowserContext,
  type Locator,
} from "@playwright/test";
import type { components } from "../src/api-schema";

export async function choose(select: Locator, value: string) {
  await select.click();
  const menu = select.page().getByRole("listbox");
  await menu
    .locator(`[role="option"][data-value=${JSON.stringify(value)}]`)
    .click();
}

export function hostStatus(
  kind: components["schemas"]["HarnessRegistration"]["harness"],
  selectable = true,
): components["schemas"]["HarnessStatus"] {
  return {
    registration: {
      harness: kind,
      revision: 1,
      executable: null,
      config_directory: null,
    },
    launch: {
      harness: kind,
      registration_revision: 1,
      native_executable: `/fixture/${kind}`,
      executable_source: "path",
      config_directory: `/fixture/config/${kind}`,
      config_source: "default",
      runtime_executable: selectable ? `/fixture/runtime/${kind}` : null,
      adapter_version: "fixture",
    },
    native_installed: true,
    config_available: true,
    selectable,
    authentication: "unknown",
    model_access: "unverified",
    native_version: null,
    checked: false,
    problems: selectable ? [] : ["native_missing"],
    catalog_ownership: null,
  };
}

// Ordinary browser checks never discover or start an installed agent.
export const test = base.extend({
  context: async ({ context }, provide) => {
    // Register before any page opens: native tabs can load before their page event.
    await stubModelCatalog(context);
    await context.route("**/api/harnesses", (route) =>
      route.fulfill({
        json: [hostStatus("codex"), hostStatus("claude-code", false)],
      }),
    );
    await provide(context);
  },
});

export async function stubModelCatalog(context: BrowserContext) {
  await context.route("**/api/worker-models*", (route) =>
    route.fulfill({ json: [] }),
  );
}

export function existingDirectory(path: string) {
  mkdirSync(path, { recursive: true });
  return path;
}

export const state = process.env.FLOWFIELD_SMOKE_STATE!;

export const checkout = resolve("..");

export function cli(args: string[], cwd?: string) {
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "--project",
        checkout,
        "flowfield",
        "--data-dir",
        state,
        "--port",
        "8766",
        ...args,
        "--json",
      ],
      { encoding: "utf8", cwd },
    ),
  );
}

export async function closeOverlay(page: Page) {
  for (let depth = 0; depth < 6; depth++) {
    const dialog = page
      .locator(
        "[data-slot=dialog-content][data-state=open]:not(.suspended), .entity-pane",
      )
      .last();
    if (!(await dialog.count())) return;
    const url = page.url();
    try {
      await dialog.locator(".close-control").first().click({ timeout: 1000 });
    } catch (error) {
      // Archive/Restore may finish closing while the click waits for stability.
      if (await dialog.count()) throw error;
    }
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect
      .poll(async () => page.url() !== url || (await dialog.count()) === 0)
      .toBe(true);
  }
  await expect(
    page.locator("[data-slot=dialog-content][data-state=open]"),
  ).toHaveCount(0);
}

export async function ensureEditing(page: Page) {
  const button = page.getByRole("button", { name: "Edit", exact: true });
  const field = page
    .getByLabel("Title", { exact: true })
    .or(page.getByLabel("Description", { exact: true }));
  await expect(button.or(field).first()).toBeAttached();
  if (await button.isVisible()) await button.click();
}

export async function connectMcp(request: APIRequestContext) {
  // Browser interactions can leave this separate Node client idle at the server's
  // keep-alive deadline. Do not pool fixture-write sockets across those gaps or
  // retry mutations after an ambiguous connection reset.
  const headers = {
    Accept: "application/json, text/event-stream",
    Connection: "close",
  };
  const initialized = await request.post("/mcp/", {
    headers,
    data: {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-11-25",
        capabilities: {},
        clientInfo: { name: "browser-test", version: "1" },
      },
    },
  });
  expect(initialized.ok()).toBe(true);
  const protocol = (await initialized.json()).result.protocolVersion;
  const connectedHeaders = { ...headers, "MCP-Protocol-Version": protocol };
  await request.post("/mcp/", {
    headers: connectedHeaders,
    data: { jsonrpc: "2.0", method: "notifications/initialized" },
  });
  return async (name: string, args: Record<string, unknown>) => {
    const response = await request.post("/mcp/", {
      headers: connectedHeaders,
      data: {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name, arguments: args },
      },
    });
    expect(response.ok()).toBe(true);
    const result = (await response.json()).result;
    expect(result.isError).toBe(false);
    return result.structuredContent;
  };
}

export function resultMessage(v: {
  id: string;
  version: number;
  revision: number;
  created_at: string;
  report: { summary: string } | null;
}) {
  return {
    id: "result:" + v.id,
    kind: "result",
    source_id: v.id,
    revision: v.revision,
    created_at: v.created_at,
    author: "service",
    actor: { role: "flowfield", label: "Flowfield", identity: "service" },
    stages: [],
    stage_changes: [],
    state: { label: "Recorded", tone: "complete", href: null },
    title: "Result " + v.version,
    body: v.report?.summary ?? "",
    truncated: false,
    status: null,
    result_id: v.id,
    run_id: null,
  };
}

export function fixtureStages() {
  return [
    {
      id: "fixture",
      title: "Fixture setup",
      outcome: "Prepare the isolated fixture",
      status: "completed",
    },
  ];
}

// Model-free historical states are fixture setup, not coordinator MCP capabilities.
export async function fixtureProgress(
  request: APIRequestContext,
  input: {
    project_id: string;
    task_id: string;
    progress: Record<string, unknown>;
  },
) {
  const response = await request.post(
    `/api/projects/${input.project_id}/tasks/${input.task_id}/progress`,
    { data: input.progress },
  );
  expect(response.ok()).toBe(true);
  return response.json();
}
