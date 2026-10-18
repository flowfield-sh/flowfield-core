import { expect } from "@playwright/test";
import {
  test,
  choose,
  fixtureProject,
  projectWorkspace,
  existingDirectory,
  state,
} from "./support";
import { join } from "node:path";

const model = {
  id: "test",
  name: "Test model",
  efforts: ["low"],
  modes: [{ id: "agent", name: "Workspace access" }],
  fast: true,
};
const choice = {
  harness: "codex",
  model: "test",
  effort: "low",
  mode: "agent",
  fast: false,
};

test("workspace setup keeps three explicit role choices in a consistent pane", async ({
  page,
}, testInfo) => {
  await page.route("**/api/worker-models*", (route) =>
    route.fulfill({ json: [model] }),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Workspaces", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("home-desktop.png"),
    animations: "disabled",
  });
  await page.getByRole("link", { name: "New workspace", exact: true }).click();
  const form = page.getByRole("region", { name: "New workspace form" });
  await form.getByLabel("Name", { exact: true }).fill("Release workspace");
  for (const role of ["Coordinator", "Worker", "Reviewer"]) {
    const fields = form.getByRole("group", { name: role, exact: true });
    await expect(
      fields.getByRole("combobox", { name: "Harness", exact: true }),
    ).toHaveAttribute("data-value", "");
    await expect(
      form.getByRole("button", { name: "Create workspace" }),
    ).toBeDisabled();
    await choose(
      fields.getByRole("combobox", { name: "Harness", exact: true }),
      "codex",
    );
    await choose(
      fields.getByRole("combobox", { name: "Model", exact: true }),
      "test",
    );
    await choose(
      fields.getByRole("combobox", { name: "Reasoning effort", exact: true }),
      "low",
    );
    await choose(
      fields.getByRole("combobox", { name: "Access mode", exact: true }),
      "agent",
    );
  }
  await expect(
    form.getByRole("button", { name: "Create workspace" }),
  ).toBeEnabled();
  for (const [width, theme] of [
    [1440, "light"],
    [390, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: theme });
    if (theme === "dark")
      await expect(page.locator("html")).toHaveClass(/dark/);
    await form.getByRole("group", { name: "Coordinator", exact: true }).hover();
    await page.mouse.wheel(0, -3000);
    await page.screenshot({
      path: testInfo.outputPath(`workspace-setup-${width}.png`),
      animations: "disabled",
    });
    await page.mouse.wheel(0, 3000);
    await expect(
      form.getByRole("button", { name: "Create workspace" }),
    ).toBeInViewport();
    await page.screenshot({
      path: testInfo.outputPath(`workspace-setup-end-${width}.png`),
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});

test("task reviewer uses an inline Fast checkbox and Reset with retained choices", async ({
  page,
  request,
}, testInfo) => {
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, "reviewer-fast")) },
  });
  const workspace = await projectWorkspace(request, "reviewer-fast");
  const task = await (
    await request.post(`/api/workspaces/${workspace}/tasks`, {
      data: { title: "Verify screenshot" },
    })
  ).json();
  await page.route(`**/api/workspaces/${workspace}/models*`, (route) =>
    route.fulfill({ json: [model] }),
  );
  let settings = await (
    await request.get(`/api/workspaces/${workspace}/reviewer?entity=${task.id}`)
  ).json();
  settings.effective.agent = {
    choice,
    source: "workspace",
    default_revision: 1,
    override_revision: 1,
  };
  await page.route(
    `**/api/workspaces/${workspace}/reviewer?entity=*`,
    async (route) => {
      if (route.request().method() === "PUT") {
        const data = route.request().postDataJSON();
        expect(data.expected_revision).toBe(settings.revision);
        settings = {
          ...settings,
          ...data,
          revision: settings.revision + 1,
          effective: {
            ...settings.effective,
            enabled: data.enabled ?? true,
            agent: {
              ...settings.effective.agent,
              choice: data.selection ?? choice,
              source: data.selection ? "override" : "workspace",
            },
          },
        };
      }
      await route.fulfill({ json: settings });
    },
  );
  await page.goto(`/workspaces/${workspace}/tasks/${task.key}/settings`);
  const reviewer = page.getByRole("region", {
    name: "Reviewer settings",
    exact: true,
  });
  const fast = reviewer.getByRole("checkbox", {
    name: "Fast mode",
    exact: true,
  });
  await expect(fast).not.toBeChecked();
  await expect(
    reviewer.getByRole("button", { name: "Cancel", exact: true }),
  ).toHaveCount(0);
  await fast.check();
  await reviewer.getByRole("button", { name: "Save", exact: true }).click();
  await expect(
    reviewer.getByRole("button", { name: "Save", exact: true }),
  ).toBeDisabled();
  expect(settings.selection.fast).toBe(true);
  await page.reload();
  await expect(fast).toBeChecked();
  for (const [width, theme] of [
    [1440, "light"],
    [390, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: theme });
    if (theme === "dark")
      await expect(page.locator("html")).toHaveClass(/dark/);
    await fast.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath(`reviewer-settings-${width}.png`),
      animations: "disabled",
    });
  }
  await reviewer.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(fast).not.toBeChecked();
  expect(settings.selection).toBeNull();
});
