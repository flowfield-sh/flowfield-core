import { openResource } from "./shell-navigation";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

test("explicit workspace creation, project adoption and shared draft navigation", async ({
  page,
  request,
}, testInfo) => {
  const before = await (await request.get("/api/workspaces")).json();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Workspaces", exact: true }),
  ).toBeVisible();
  expect(await (await request.get("/api/workspaces")).json()).toEqual(
    expect.arrayContaining(before),
  );
  await page.getByRole("link", { name: "New workspace", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Product development");
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing" }).click();
  await page
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(
    page.locator('select[aria-label="Workspace"] option:checked'),
  ).toHaveText("Product development");
  await expect(
    page.getByRole("region", { name: "Coordinator conversation", exact: true }),
  ).toBeVisible();
  const spaces = await (await request.get("/api/workspaces")).json();
  expect(
    spaces.filter(
      (item: { name: string }) => item.name === "Product development",
    ),
  ).toHaveLength(1);
  const workspace = spaces.find(
    (item: { name: string }) => item.name === "Product development",
  );
  expect(
    await (
      await request.get(`/api/workspaces/${workspace.id}/projects`)
    ).json(),
  ).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("workspace-empty-desktop-light.png"),
  });
  await openResource(page, "Projects");
  await page.getByRole("link", { name: "Add project", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Add a project", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await openResource(page, "Projects");
  await expect(page).toHaveURL(`/workspaces/${workspace.id}/projects`);
  await page.getByRole("link", { name: "Add project", exact: true }).click();
  let path: string | null = null;
  await page.route("**/api/projects/select-directory", (route) =>
    route.fulfill({ json: { path } }),
  );
  await page
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await expect(
    page.getByLabel("Project directory", { exact: true }),
  ).toHaveCount(0);
  path = join(state, "entry-core");
  mkdirSync(path, { recursive: true });
  writeFileSync(join(path, "AGENTS.md"), "Keep these project conventions.\n");
  await page
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await expect(
    page.getByLabel("Project directory", { exact: true }),
  ).toHaveValue(path);
  await expect(
    page.getByText("Discuss its existing", { exact: false }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("workspace-adoption-desktop-light.png"),
  });
  await page.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL(
    `/workspaces/${workspace.id}/tasks?project=entry-core`,
  );
  expect(readFileSync(join(path, "AGENTS.md"), "utf8")).toBe(
    "Keep these project conventions.\n",
  );
  expect(existsSync(join(path, ".agents"))).toBe(false);
  const composer = page.getByRole("textbox", {
    name: "Message coordinator",
    exact: true,
  });
  await composer.fill("Plan the shared release");
  await openResource(page, "Projects");
  await expect(composer).toHaveValue("Plan the shared release");
  await expect(page.getByRole("link", { name: /entry-core/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "Projects", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.screenshot({
    path: testInfo.outputPath("workspace-projects-narrow-dark.png"),
  });
});
