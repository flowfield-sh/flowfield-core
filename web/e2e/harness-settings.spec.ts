import { test } from "./support";
import { expect, type Page } from "@playwright/test";

function host(kind: "codex" | "claude-code") {
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
      bridge_executable: `/service/bridges/${kind}/runtime`,
      bridge_version: "fixture.1",
    },
    native_installed: true,
    config_available: true,
    bridge_installed: true,
    selectable: true,
    authentication: "unknown",
    model_access: "unverified",
    native_version: null as string | null,
    checked: false,
    problems: [] as string[],
    installing: false,
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

async function screenshot(page: Page, name: string) {
  if (process.env.FLOWFIELD_VISUAL_EVIDENCE) {
    await page.locator(".harness-settings-page").evaluate((element) => {
      element.scrollTop = 0;
    });
    await page.screenshot({
      path: `/private/tmp/flowfield-h1-settings-${name}.png`,
      fullPage: true,
    });
  }
}

test("central harness settings preserve host drafts and configure kinds independently without native discovery", async ({
  page,
}) => {
  const codex = host("codex"),
    claude = host("claude-code");
  codex.registration.executable = "/service/custom/codex";
  let discoveries = 0;
  await page.route("**/api/worker-models*", (route) => {
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
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/harnesses$/);
  const first = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  const second = page.getByRole("region", {
    name: "Claude Code settings",
    exact: true,
  });
  await expect(
    first.getByLabel("Executable override", { exact: true }),
  ).toHaveValue("/service/custom/codex");
  await expect(second).toContainText("Setup detected");
  await second
    .getByLabel("Executable override", { exact: true })
    .fill("/service/alternative/claude");
  await second
    .getByLabel("Configuration directory override", { exact: true })
    .fill("/service/claude-config");
  await expect(
    second.getByRole("button", { name: "Check saved setup" }),
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
  await screenshot(page, "desktop");
  await second
    .getByLabel("Executable override", { exact: true })
    .fill("/unsaved");
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing" }).click();
  await expect(
    second.getByLabel("Executable override", { exact: true }),
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
  const executable = entry.getByLabel("Executable override", { exact: true });
  await executable.fill("/service/my-draft/codex");
  await entry.getByRole("button", { name: "Save paths", exact: true }).click();
  await expect(entry).toContainText("Host settings changed elsewhere.");
  await expect(executable).toHaveValue("/service/my-draft/codex");
  page.once("dialog", (dialog) => dialog.accept());
  await entry.getByRole("button", { name: "Load latest", exact: true }).click();
  await expect(executable).toHaveValue("/service/external/codex");
  await expect(
    entry.getByRole("button", { name: "Save paths", exact: true }),
  ).toBeDisabled();
});

test("explicit host installation, readiness and exact interrupted-discovery confirmation", async ({
  page,
}) => {
  const codex = host("codex"),
    claude = host("claude-code");
  codex.bridge_installed = false;
  codex.catalog_ownership = {
    id: "interrupted-exact-id",
    harness: "codex",
    project_id: "harbor",
    status: "uncertain",
    started_at: "2026-10-09T00:00:00Z",
    launch: codex.launch,
    operation: "models",
  };
  const operations: string[] = [];
  await page.route("**/api/harnesses/**", async (route) => {
    const url = new URL(route.request().url());
    const value = url.pathname.includes("claude-code") ? claude : codex;
    if (route.request().method() === "POST") {
      operations.push(url.pathname);
      if (url.pathname.endsWith("/install")) value.bridge_installed = true;
      if (url.pathname.endsWith("/check"))
        Object.assign(value, {
          checked: true,
          authentication: "authenticated",
          native_version: "fixture.native",
        });
      if (url.pathname.endsWith("/confirm-stopped")) {
        expect(route.request().postDataJSON()).toEqual({
          id: "interrupted-exact-id",
        });
        value.catalog_ownership = null;
      }
    }
    await route.fulfill({ json: value });
  });
  await page.goto("/settings/harnesses");
  const entry = page.getByRole("region", {
    name: "Codex settings",
    exact: true,
  });
  await entry
    .getByRole("button", { name: "Install bridge", exact: true })
    .click();
  await expect(
    entry.getByRole("button", { name: "Install bridge", exact: true }),
  ).toHaveCount(0);
  await entry.getByRole("button", { name: "Check saved setup" }).click();
  await expect(entry).toContainText(
    "Setup checked. Model access still needs a successful turn.",
  );
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
  await expect(entry).toContainText("Discovery hold cleared");
  expect(operations).toEqual([
    "/api/harnesses/codex/install",
    "/api/harnesses/codex/check",
    "/api/harnesses/codex/catalog/confirm-stopped",
  ]);
});

test("mobile settings close project navigation and keep long host paths inside the page", async ({
  page,
}) => {
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
    .getByRole("button", { name: "Open projects", exact: true })
    .click();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close projects", exact: true }),
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
  await screenshot(page, "mobile");
});
