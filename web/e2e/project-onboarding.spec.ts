import { openResource } from "./shell-navigation";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureProject, projectWorkspace } from "./support";

test("directory adoption preserves guidance, cancellation and required identity fields", async ({
  page,
  request,
}, testInfo) => {
  const directory = join(state, "native-directory-project");
  mkdirSync(directory, { recursive: true });
  writeFileSync(
    join(directory, "AGENTS.md"),
    "Keep the repository conventions.\n",
  );
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Directory adoption",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  let path: string | null = null;
  await page.route("**/api/projects/select-directory", (route) => {
    expect(route.request().method()).toBe("POST");
    return route.fulfill({ json: { path } });
  });
  await page.goto(`/workspaces/${workspace.id}/projects/new`);
  const setup = page.getByRole("region", {
    name: "Project setup instructions",
  });
  await setup
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await expect(setup.getByLabel("Project directory")).toHaveCount(0);
  expect(existsSync(join(directory, ".flowfield"))).toBe(false);
  path = directory;
  await setup
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await expect(setup.getByLabel("Project directory")).toHaveValue(directory);
  await expect(setup.getByLabel("Project directory")).toHaveAttribute(
    "readonly",
    "",
  );
  for (const [label, value] of [
    ["Name", "native-directory-project"],
    ["Project ID", "native-directory-project"],
  ]) {
    const field = setup.getByRole("textbox", { name: label, exact: true });
    await expect(field).toHaveValue(value);
    await expect(field).toHaveAttribute("required", "");
    await field.fill("");
    await setup
      .getByRole("button", { name: "Add project", exact: true })
      .click();
    await expect(field).toBeFocused();
    expect(existsSync(join(directory, ".flowfield"))).toBe(false);
    await field.fill(value);
  }
  await expect(setup).toContainText(
    "Discuss its existing instructions with the Coordinator",
  );
  await expect(setup.getByLabel("Install project guidance")).toHaveCount(0);
  await expect(setup.getByRole("button", { name: /settings/ })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("adoption-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(setup.getByLabel("Name", { exact: true })).toHaveCSS(
    "color",
    "rgb(227, 231, 236)",
  );
  expect(await setup.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
  await page.screenshot({
    path: testInfo.outputPath("adoption-mobile-dark.png"),
    animations: "disabled",
  });
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL(
    `/workspaces/${workspace.id}/tasks?project=native-directory-project`,
  );
  expect(readFileSync(join(directory, "AGENTS.md"), "utf8")).toBe(
    "Keep the repository conventions.\n",
  );
  expect(existsSync(join(directory, ".agents"))).toBe(false);
  const saved = await (
    await request.get("/api/projects/native-directory-project/integration")
  ).json();
  expect(saved.runtime).toBe("local");
  expect(saved.checks).toEqual([]);
  expect(saved.target_branch).toBeNull();
  const workers = await (
    await request.get(`/api/workspaces/${workspace.id}/workers`)
  ).json();
  expect(workers.selection.model).toBe("test-model");
  expect(workers.enabled).toBe(false);
});

test("registration conflicts retain the selected directory and allow identity correction", async ({
  page,
  request,
}) => {
  const existing = join(state, "first", "picked-collision");
  const selected = join(state, "second", "picked-collision");
  mkdirSync(existing, { recursive: true });
  mkdirSync(selected, { recursive: true });
  expect(
    (await fixtureProject(request, { data: { path: existing } })).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, "picked-collision");
  await page.route("**/api/projects/select-directory", (route) =>
    route.fulfill({ json: { path: selected } }),
  );
  await page.goto(`/workspaces/${workspace}/projects/new`);
  const setup = page.getByRole("region", {
    name: "Project setup instructions",
  });
  await setup
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("another directory");
  expect(existsSync(join(selected, ".flowfield"))).toBe(false);
  await setup.getByLabel("Project ID", { exact: true }).fill("picked-unique");
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL(
    `/workspaces/${workspace}/tasks?project=picked-unique`,
  );
  expect(existsSync(join(selected, "AGENTS.md"))).toBe(false);
  await openResource(page, "Projects");
  await expect(
    page.locator(`a[href="/projects/picked-unique/overview"]`),
  ).toBeVisible();
});
