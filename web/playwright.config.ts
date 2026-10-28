import { defineConfig } from "@playwright/test";

if (process.env.FLOWFIELD_SMOKE_STATE || process.env.FLOWFIELD_SMOKE_PORT)
  throw new Error(
    "Browser tests allocate isolated stores/ports. Use FLOWFIELD_SMOKE_RETAIN=1 to retain their stores.",
  );

export default defineConfig({
  testDir: "./e2e",
  workers: 2,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  projects: [
    {
      name: "journeys",
      use: { channel: "chromium" },
      testIgnore: ["notifications.spec.ts", "workspace-events.spec.ts"],
    },
    {
      name: "notifications",
      use: { channel: "chromium" },
      testMatch: ["notifications.spec.ts", "workspace-events.spec.ts"],
    },
    {
      name: "firefox-recovery",
      testMatch: ["stream-recovery.spec.ts"],
      use: { browserName: "firefox" },
    },
  ],
  use: { headless: true, browserName: "chromium", trace: "retain-on-failure" },
});
