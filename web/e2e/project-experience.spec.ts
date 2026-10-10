import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, existingDirectory, state } from "./support";

test("home, welcome and editable settings form a complete project journey", async ({
  page,
  request,
}, testInfo) => {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Set up your project" }),
  ).toBeVisible();
  await page.unroute("**/api/projects");
  const project = "project-experience";
  const response = await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project)),
      task_prefix: "UXA",
      name: "Garden planner",
    },
  });
  expect(response.ok()).toBe(true);
  await page.reload();
  const home = page.getByRole("region", { name: "Projects", exact: true });
  await expect(home.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Set up your project" }),
  ).toHaveCount(0);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("projects-desktop.png"),
  });
  const rows = home.locator(".project-list-row");
  expect(
    await rows.evaluateAll((nodes) =>
      nodes
        .slice(1)
        .every(
          (row, i) =>
            Math.abs(
              row.getBoundingClientRect().top -
                nodes[i].getBoundingClientRect().bottom,
            ) < 1,
        ),
    ),
  ).toBe(true);
  await home.getByRole("link", { name: /Garden planner/ }).click();
  const welcome = page.getByRole("article", { name: "Coordinator welcome" });
  await expect(welcome).toContainText("Starting fresh?");
  await expect(welcome).not.toContainText("Coordinator · Welcome");
  await expect(welcome).toContainText("Existing project?");
  await expect(welcome).toContainText("Project settings → Integration");
  expect(
    (await (await request.get(`/api/projects/${project}/coordinator`)).json())
      .items,
  ).toEqual([]);
  await page.reload();
  await expect(welcome).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-welcome.png"),
  });

  // Exercise the queue UI without permitting a native worker launch.
  let workers = {
    project_id: project,
    revision: 1,
    enabled: false,
    selection: {
      harness: "codex",
      model: "test-model",
      effort: "low",
      mode: null,
      fast: false,
    },
    max_parallel: 1,
    problem: null,
  };
  await page.route(`**/api/projects/${project}/workers`, (route) =>
    route.fulfill({ json: workers }),
  );
  await page.route(`**/api/projects/${project}/queue`, (route) => {
    const body = route.request().postDataJSON();
    expect(body.expected_revision).toBe(workers.revision);
    workers = {
      ...workers,
      enabled: body.enabled,
      revision: workers.revision + 1,
    };
    return route.fulfill({ json: workers });
  });
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  const editor = page.getByRole("region", {
    name: "Edit project",
    exact: true,
  });
  await expect(
    editor.getByRole("tab", { name: "General", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    editor.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  await editor.getByLabel("Name", { exact: true }).fill("Garden plans");
  await editor.getByRole("tab", { name: "Integration", exact: true }).click();
  await editor
    .getByLabel("Validation commands", { exact: true })
    .fill("pnpm check");
  await expect(
    editor.getByLabel("Validation commands", { exact: true }),
  ).toHaveAccessibleDescription(
    "Required before approval. One shell command per line.",
  );
  await expect(
    editor.getByRole("tab", { name: "Integration", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("integration-hints-desktop.png"),
  });
  await editor.getByRole("tab", { name: "General", exact: true }).click();
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Garden plans",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Garden plans",
  );
  await expect(
    editor.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeDisabled();
  const savedToast = page
    .locator("[data-sonner-toast][data-type=success]")
    .filter({ hasText: "Changes saved." });
  await expect(savedToast).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("save-toast-desktop.png"),
  });
  await expect(savedToast.locator("[data-close-button]")).toHaveAccessibleName(
    "Close toast",
  );
  await savedToast.locator("[data-close-button]").click();
  await expect(savedToast).toHaveCount(0);
  await expect(editor).toBeVisible();
  await expect(editor.locator('[data-slot="separator"]')).toBeVisible();
  await editor.getByLabel("Run worker queue", { exact: true }).click();
  await expect(
    editor.getByLabel("Run worker queue", { exact: true }),
  ).toBeChecked();
  await editor.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(
    editor.getByLabel("Validation commands", { exact: true }),
  ).toHaveValue("pnpm check");
  await editor.getByLabel("Validation commands", { exact: true }).fill("");
  await editor.getByRole("tab", { name: "General", exact: true }).click();
  await page.reload();
  await expect(
    editor.getByLabel("Run worker queue", { exact: true }),
  ).toBeChecked();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("general-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("general-mobile.png"),
  });
  await editor.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(
    editor.getByRole("tab", { name: "Integration", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("integration-hints-mobile.png"),
  });
  await page.goto("/");
  await expect(home.getByRole("link", { name: /Garden plans/ })).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("projects-mobile.png"),
  });
  await home.getByRole("link", { name: "New project", exact: true }).click();
  await expect(page).toHaveURL("/new-project");
  await expect(
    page.getByRole("heading", { name: "Set up your project" }),
  ).toBeVisible();
});

test("settings toasts preserve drafts, support keyboard dismissal and expire across navigation", async ({
  page,
  request,
}, testInfo) => {
  const project = "toast-settings";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project)),
      task_prefix: "TST",
      name: "Toast settings",
    },
  });
  await page.addInitScript(() =>
    localStorage.setItem("flowfield.theme", "dark"),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects/${project}/edit/general`);
  const editor = page.getByRole("region", {
    name: "Edit project",
    exact: true,
  });
  await editor.getByLabel("Name", { exact: true }).fill("Preserved name");
  const failureText =
    "Settings could not be saved. Try again when the service is available.";
  await page.route(`**/api/projects/${project}`, async (route) => {
    if (route.request().method() === "PUT")
      return route.fulfill({
        status: 503,
        json: { error: { message: failureText } },
      });
    await route.continue();
  });
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  const failure = page.locator("[data-sonner-toast][data-type=error]");
  await expect(failure).toContainText(failureText);
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Preserved name",
  );
  await expect(editor.getByText(failureText)).toHaveCount(0);
  await expect(page.locator("[data-sonner-toaster]")).toHaveAttribute(
    "data-sonner-theme",
    "dark",
  );
  await page.keyboard.press("Alt+t");
  await expect(page.locator("[data-sonner-toaster]")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(failure).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    failure.getByRole("button", { name: "Close toast" }),
  ).toBeFocused();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("error-toast-mobile-dark.png"),
  });
  const bounds = await failure.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.keyboard.press("Enter");
  await expect(failure).toHaveCount(0);
  await expect(editor).toBeVisible();
  await page.unroute(`**/api/projects/${project}`);
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  const success = page.locator("[data-sonner-toast][data-type=success]");
  await expect(success).toContainText("Changes saved.");
  await page
    .getByRole("dialog", { name: "Project details", exact: true })
    .getByRole("button", { name: "Close editor", exact: true })
    .click();
  await expect(editor).toHaveCount(0);
  await expect(success).toBeVisible();
  await page.mouse.move(1, 1);
  await expect(success).toHaveCount(0, { timeout: 8000 });
});
