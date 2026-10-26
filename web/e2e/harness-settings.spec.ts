import { test } from "./support";
import {
  expect,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";

function host(kind: "codex" | "claude-code" | "pi") {
  return {
    registration: {
      harness: kind,
      revision: 1,
      executable: null as string | null,
      config_directory: null as string | null,
    },
    launch: {
      harness: kind,
      registration_revision: 1,
      native_executable: `/service/bin/${kind}`,
      executable_source: "path",
      config_directory: `/service/config/${kind}`,
      config_source: "default",
      runtime_executable: `/service/runtime/${kind}/runtime`,
      adapter_version: "fixture.1",
    },
    native_installed: true,
    config_available: true,
    selectable: true,
    authentication: "unknown",
    model_access: "unverified",
    native_version: null as string | null,
    checked: false,
    checked_at: null as string | null,
    problems: [] as string[],
    catalog_ownership: null as null | {
      id: string;
      harness: string;
      project_id: string | null;
      status: string;
      started_at: string;
      launch: unknown;
      operation: string;
    },
  };
}

async function screenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  target?: Locator,
) {
  if (process.env.FLOWFIELD_VISUAL_EVIDENCE) {
    if (target) await target.scrollIntoViewIfNeeded();
    else
      await page.locator(".harness-settings-page").evaluate((element) => {
        element.scrollTop = 0;
      });
    await page.screenshot({
      path: testInfo.outputPath(`harness-settings-${name}.png`),
      fullPage: true,
    });
  }
}

for (const [kind, name] of [
  ["codex", "Codex"],
  ["claude-code", "Claude Code"],
  ["pi", "Pi"],
] as const) {
  test(`${name} first setup can detect a new install and reports failed checks honestly`, async ({
    page,
  }, testInfo) => {
    const value = host(kind);
    value.native_installed = false;
    value.selectable = false;
    let checked = false;
    await page.route(`**/api/harnesses/${kind}`, (route) =>
      route.fulfill({ json: value }),
    );
    await page.route(`**/api/harnesses/${kind}/check`, (route) => {
      checked = true;
      value.checked = true;
      value.checked_at = new Date().toISOString();
      return route.fulfill({ json: value });
    });
    await page.goto("/settings/harnesses");
    const entry = page.getByRole("region", {
      name: `${name} settings`,
      exact: true,
    });
    await expect(entry).toContainText("Native harness missing");
    await expect(
      entry.getByRole("button", { name: "Verify installation" }),
    ).toBeEnabled();
    expect(checked).toBe(false);
    await entry.getByRole("button", { name: "Verify installation" }).click();
    await expect(
      page.locator("[data-sonner-toast][data-type=warning]").last(),
    ).toContainText(`${name} installation needs attention.`);
    await expect(
      entry.getByRole("heading", { name: "Native harness missing" }),
    ).toBeVisible();
    value.native_installed = true;
    value.selectable = true;
    value.problems = ["native_check_failed"];
    await entry.getByRole("button", { name: "Verify installation" }).click();
    await expect(entry).toContainText("Installation verification failed");
    await expect(
      page.locator("[data-sonner-toast][data-type=warning]").last(),
    ).toContainText(`${name} installation needs attention.`);
    await expect(
      page.locator("[data-sonner-toast][data-type=success]"),
    ).toHaveCount(0);
    await expect(entry).toContainText("The installation check failed");
    await expect(
      entry.getByRole("button", { name: "Verify installation" }),
    ).toBeEnabled();
    await screenshot(page, testInfo, `${kind}-failed-check`, entry);
    value.problems = kind === "pi" ? [] : ["native_login_required"];
    value.authentication = kind === "pi" ? "unknown" : "signed-out";
    value.native_version = "fixture.native";
    await entry.getByRole("button", { name: "Verify installation" }).click();
    if (kind === "pi")
      await expect(
        page.locator("[data-sonner-toast][data-type=success]"),
      ).toContainText(`${name} installation verified.`);
    else await expect(entry).toContainText("Sign-in required");
    if (kind !== "pi") {
      value.problems = [];
      value.authentication = "authenticated";
      await entry.getByRole("button", { name: "Verify installation" }).click();
      await expect(
        page.locator("[data-sonner-toast][data-type=success]"),
      ).toContainText(`${name} installation verified.`);
    }
    await expect(
      entry.getByRole("heading", {
        name: "Installation verified",
        exact: true,
      }),
    ).toBeVisible();
    await expect(entry).toContainText(
      kind === "pi"
        ? "Provider sign-in is checked when you use a model."
        : "Model access requires a successful model request.",
    );
    const checkedAt = value.checked_at!;
    await page.reload();
    await expect(
      entry.getByRole("heading", {
        name: "Installation verified",
        exact: true,
      }),
    ).toBeVisible();
    await expect(entry.locator("time").first()).toHaveAttribute(
      "datetime",
      checkedAt,
    );
    await screenshot(page, testInfo, `${kind}-verified`, entry);
    await entry.getByText("Detected setup", { exact: true }).click();
    await expect(entry).toContainText("fixture.native");
    if (kind !== "pi")
      await expect(entry.getByText("Signed in", { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.reload();
    await expect(
      entry.getByText("Last checked", { exact: false }),
    ).toBeVisible();
    await screenshot(page, testInfo, `${kind}-retained-mobile-dark`, entry);
    value.checked = false;
    value.checked_at = null;
    value.native_version = null;
    value.authentication = "unknown";
    await page.reload();
    await expect(
      entry.getByRole("heading", { name: "Installation detected" }),
    ).toBeVisible();
    await expect(entry.locator("time")).toHaveCount(0);
  });
}

test("central harness settings preserve host drafts and configure kinds independently without native discovery", async ({
  page,
}, testInfo) => {
  const codex = host("codex"),
    claude = host("claude-code");
  codex.registration.executable = "/service/custom/codex";
  let discoveries = 0;
  await page.route("**/api/{worker-models,workspaces/*/models}*", (route) => {
    discoveries++;
    return route.fulfill({ json: [] });
  });
  const writes: unknown[] = [];
  await page.route("**/api/harnesses/*", async (route) => {
    const value = route.request().url().includes("claude-code")
      ? claude
      : codex;
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      writes.push(body);
      expect(body.expected_revision).toBe(value.registration.revision);
      Object.assign(value.registration, {
        revision: value.registration.revision + 1,
        executable: body.executable,
        config_directory: body.config_directory,
      });
      value.launch.native_executable =
        body.executable ?? `/service/bin/${value.registration.harness}`;
      value.launch.config_directory =
        body.config_directory ??
        `/service/config/${value.registration.harness}`;
      return route.fulfill({ json: value.registration });
    }
    return route.fulfill({ json: value });
  });
  await page.goto("/");
  await page
    .getByRole("link", { name: "Flowfield settings", exact: true })
    .click();
  await expect(page).toHaveURL(/\/settings\/harnesses$/);
  const first = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  const second = page.getByRole("region", {
    name: "Claude Code settings",
    exact: true,
  });
  await first.getByText("Path overrides", { exact: true }).click();
  await second.getByText("Path overrides", { exact: true }).click();
  await expect(
    first.getByLabel("Executable path", { exact: true }),
  ).toHaveValue("/service/custom/codex");
  await expect(second).toContainText("Installation detected");
  await second.getByText("Detected setup", { exact: true }).click();
  await expect(second).toContainText("Not verified");
  await second
    .getByLabel("Executable path", { exact: true })
    .fill("/service/alternative/claude");
  await second
    .getByLabel("Configuration directory", { exact: true })
    .fill("/service/claude-config");
  await page.getByRole("tab", { name: "Appearance", exact: true }).click();
  await expect(page.getByLabel("Theme", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Harnesses", exact: true }).click();
  await expect(
    second.getByLabel("Configuration directory", { exact: true }),
  ).toHaveValue("/service/claude-config");
  await expect(
    second.getByRole("button", { name: "Verify installation" }),
  ).toBeDisabled();
  await second.getByRole("button", { name: "Save paths" }).click();
  await expect
    .poll(() => writes)
    .toEqual([
      {
        expected_revision: 1,
        executable: "/service/alternative/claude",
        config_directory: "/service/claude-config",
      },
    ]);
  await expect(
    second.getByRole("button", { name: "Save paths" }),
  ).toBeDisabled();
  expect(codex.registration.revision).toBe(1);
  await screenshot(page, testInfo, "desktop");
  await second.getByLabel("Executable path", { exact: true }).fill("/unsaved");
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing" }).click();
  await expect(
    second.getByLabel("Executable path", { exact: true }),
  ).toHaveValue("/unsaved");
  expect(discoveries).toBe(0);
});

test("host configuration conflicts preserve drafts until explicit reload", async ({
  page,
}) => {
  const codex = host("codex");
  await page.route("**/api/harnesses/*", (route) => {
    if (route.request().method() === "PUT") {
      codex.registration.revision = 2;
      codex.registration.executable = "/service/external/codex";
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "revision_conflict",
            message: "Host settings changed elsewhere.",
          },
        },
      });
    }
    return route.fulfill({
      json: route.request().url().endsWith("/codex")
        ? codex
        : host("claude-code"),
    });
  });
  await page.goto("/settings/harnesses");
  const entry = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  await entry.getByText("Path overrides", { exact: true }).click();
  const executable = entry.getByLabel("Executable path", { exact: true });
  await executable.fill("/service/my-draft/codex");
  await entry.getByRole("button", { name: "Save paths", exact: true }).click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Host settings changed elsewhere.");
  await expect(executable).toHaveValue("/service/my-draft/codex");
  await entry.getByRole("button", { name: "Load latest", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect(executable).toHaveValue("/service/external/codex");
  await expect(
    entry.getByRole("button", { name: "Save paths", exact: true }),
  ).toBeDisabled();
});

test("native readiness and exact interrupted-discovery confirmation", async ({
  page,
}, testInfo) => {
  const codex = host("codex"),
    claude = host("claude-code");
  codex.catalog_ownership = {
    id: "interrupted-exact-id",
    harness: "codex",
    project_id: "harbor",
    status: "uncertain",
    started_at: "2026-10-09T00:00:00Z",
    launch: codex.launch,
    operation: "models",
  };
  codex.problems = ["catalog_uncertain"];
  const operations: string[] = [];
  await page.route("**/api/harnesses/**", async (route) => {
    const url = new URL(route.request().url());
    const value = url.pathname.includes("claude-code") ? claude : codex;
    if (route.request().method() === "POST") {
      operations.push(url.pathname);
      if (url.pathname.endsWith("/check"))
        Object.assign(value, {
          checked: true,
          checked_at: new Date().toISOString(),
          authentication: "authenticated",
          native_version: "fixture.native",
        });
      if (url.pathname.endsWith("/confirm-stopped")) {
        expect(route.request().postDataJSON()).toEqual({
          id: "interrupted-exact-id",
        });
        value.catalog_ownership = null;
        value.problems = [];
      }
    }
    await route.fulfill({ json: value });
  });
  await page.goto("/settings/harnesses");
  const entry = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  await expect(entry.getByRole("button", { name: /Install/ })).toHaveCount(0);
  await entry.getByRole("button", { name: "Verify installation" }).click();
  await expect(
    page.locator("[data-sonner-toast][data-type=success]"),
  ).toContainText("Codex installation verified.");
  const installation = entry
    .getByRole("status")
    .filter({ hasText: "Installation verified" });
  const discovery = entry
    .getByRole("status")
    .filter({ hasText: "Discovery cleanup is unconfirmed" });
  await expect(installation).toBeVisible();
  await expect(
    installation.getByRole("button", { name: "Reload status" }),
  ).toHaveCount(0);
  await expect(
    discovery.getByRole("button", { name: "Reload status" }),
  ).toBeVisible();
  await screenshot(page, testInfo, "verified-with-discovery-warning", entry);
  await entry.getByText("Detected setup", { exact: true }).click();
  await expect(entry).toContainText("fixture.native");
  await entry
    .getByRole("button", { name: "Confirm discovery stopped" })
    .click();
  const confirmation = page.getByRole("alertdialog");
  await expect(
    confirmation.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await confirmation
    .getByRole("button", { name: "Confirm discovery stopped" })
    .click();
  await expect(
    page
      .locator("[data-sonner-toast][data-type=info]")
      .filter({ hasText: "Discovery hold cleared" }),
  ).toBeVisible();
  expect(operations).toEqual([
    "/api/harnesses/codex/check",
    "/api/harnesses/codex/catalog/confirm-stopped",
  ]);
});

test("mobile settings close workspace navigation and keep long host paths inside the page", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/harnesses/*", (route) => {
    const value = host(
      route.request().url().includes("claude-code") ? "claude-code" : "codex",
    );
    value.launch.config_directory =
      "/service/" + "a-long-native-configuration-directory/".repeat(8);
    return route.fulfill({ json: value });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Open workspaces", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Flowfield settings", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close workspaces", exact: true }),
  ).toHaveCount(0);
  const entry = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  await entry.getByText("Detected setup", { exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await screenshot(page, testInfo, "mobile");
});

test("appearance lives in routed settings tabs and follows system changes outside settings", async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/settings/appearance");
  await expect(
    page.getByRole("tab", { name: "Appearance", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: /^Appearance:/ })).toHaveCount(
    0,
  );
  const theme = page.getByLabel("Theme", { exact: true });
  await theme.click();
  await page.getByRole("option", { name: "Dark", exact: true }).click();
  await expect(page.locator("html")).toHaveClass("dark");
  await page.reload();
  await expect(theme).toContainText("Dark");
  await screenshot(page, testInfo, "appearance-dark");
  await theme.click();
  await page.getByRole("option", { name: "System", exact: true }).click();
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass("dark");
});

test("installation verification explains missing configuration, pending work and unverified sign-in", async ({
  page,
}, testInfo) => {
  const value = host("codex");
  value.config_available = false;
  value.problems = ["config_directory_missing"];
  let finish: () => void = () => {};
  await page.route("**/api/harnesses/codex", (route) =>
    route.fulfill({ json: value }),
  );
  await page.route("**/api/harnesses/codex/check", async (route) => {
    await new Promise<void>((resolve) => {
      finish = resolve;
    });
    await route.fulfill({ json: value });
  });
  await page.goto("/settings/harnesses");
  const entry = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  await expect(entry).toContainText(
    "Correct the configuration directory in Path overrides",
  );
  await entry
    .getByRole("button", { name: "Verify installation", exact: true })
    .click();
  await expect(
    entry.getByRole("heading", { name: "Verifying installation…" }),
  ).toBeVisible();
  await expect(
    entry.getByRole("button", { name: "Verifying installation…" }),
  ).toBeDisabled();
  await expect(entry).not.toContainText(
    "Correct the configuration directory in Path overrides",
  );
  await screenshot(page, testInfo, "verification-pending", entry);
  value.config_available = true;
  value.checked = true;
  value.checked_at = new Date().toISOString();
  value.native_version = "fixture.native";
  value.problems = ["authentication_unverified"];
  finish();
  await expect(
    entry.getByRole("heading", { name: "Sign-in could not be verified" }),
  ).toBeVisible();
  await expect(
    page.locator("[data-sonner-toast][data-type=success]"),
  ).toHaveCount(0);
  await expect(
    entry.getByRole("button", { name: "Verify installation" }),
  ).toBeEnabled();
  await screenshot(page, testInfo, "verification-sign-in-unknown", entry);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.reload();
  await expect(
    entry.getByRole("heading", { name: "Sign-in could not be verified" }),
  ).toBeVisible();
  await screenshot(
    page,
    testInfo,
    "verification-sign-in-unknown-mobile-dark",
    entry,
  );
});

test("settings tabs reveal direct links and keyboard focus when the pane narrows", async ({
  page,
}) => {
  await page.goto("/settings/updates");
  const tabs = page.getByRole("tablist", { name: "Flowfield settings" });
  const updates = tabs.getByRole("tab", { name: "Updates" });
  await expect(updates).toHaveAttribute("aria-selected", "true");
  await page.setViewportSize({ width: 320, height: 760 });
  const contained = async (tab: Locator) => {
    const frame = (await tabs.boundingBox())!;
    const target = (await tab.boundingBox())!;
    return (
      target.x >= frame.x - 1 &&
      target.x + target.width <= frame.x + frame.width + 1
    );
  };
  await expect.poll(() => contained(updates)).toBe(true);
  await page.reload();
  await expect(updates).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => contained(updates)).toBe(true);
  await updates.focus();
  await page.keyboard.press("Home");
  const harnesses = tabs.getByRole("tab", { name: "Harnesses" });
  await expect(harnesses).toBeFocused();
  await expect.poll(() => contained(harnesses)).toBe(true);
  await expect(updates).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(updates).toBeFocused();
  await expect.poll(() => contained(updates)).toBe(true);
  await tabs.hover();
  await page.mouse.wheel(0, -1000);
  await expect(tabs).toHaveAttribute("data-overflow-left", "false");
  await page.mouse.wheel(0, 1000);
  await expect(tabs).toHaveAttribute("data-overflow-right", "false");
});
