import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

const suppliedState = process.env.FLOWFIELD_SMOKE_STATE;
const state = suppliedState ?? mkdtempSync(join(tmpdir(), "flowfield-smoke-"));
process.env.FLOWFIELD_SMOKE_STATE = state;

export default defineConfig({
  testDir: "./e2e",
  workers: 2,
  forbidOnly: !!process.env.CI,
  projects: [
    {
      name: "journeys",
      testIgnore: ["notifications.spec.ts", "workspace-events.spec.ts"],
    },
    {
      name: "notifications",
      testMatch: ["notifications.spec.ts", "workspace-events.spec.ts"],
      workers: 1,
      // Service-wide preferences, delivery claims and event counts need isolated fixtures.
      dependencies: ["journeys"],
    },
  ],
  use: {
    baseURL: "http://127.0.0.1:8766",
    browserName: "chromium",
    // Exercise native tabs in the full browser's headless mode, not headless shell.
    channel: "chromium",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node e2e/server.mjs",
    env: {
      FLOWFIELD_DATA_DIR: state,
      FLOWFIELD_UPDATE_CHECKS: "0",
      FLOWFIELD_SMOKE_CLEANUP: suppliedState ? "false" : "true",
    },
    url: "http://127.0.0.1:8766/api/health",
    reuseExistingServer: false,
    timeout: 30000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10000 },
  },
});
