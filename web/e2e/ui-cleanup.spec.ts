import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
} from "./support";

test("failed resource reads and a broken event stream stay local while health is available", async ({
  page,
  request,
}) => {
  const id = "local-recovery";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  let failed = true;
  const paths = ["inspection/settings", "setup-validation"];
  const reads = new Set<string>();
  await page.route("**/api/events", (route) => route.abort());
  await page.route(`**/api/projects/${id}/**`, (route) => {
    const path = paths.find((path) =>
      route
        .request()
        .url()
        .endsWith("/" + path),
    );
    if (path) {
      reads.add(path);
      if (failed) return route.abort();
    }
    return route.continue();
  });
  await page.goto(`/projects/${id}/edit/integration`);
  const integration = page.getByRole("region", {
    name: "Integration settings",
  });
  const retries = integration.getByRole("button", {
    name: "Retry loading",
    exact: true,
  });
  await expect.poll(() => reads.size).toBe(2);
  await expect(retries).toHaveCount(2);
  await expect(
    page.getByRole("status", { name: "Reconnecting…", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("dialog", { name: "Connection lost" }),
  ).toHaveCount(0);
  // Each failed resource retains its own deliberate retry.
  failed = false;
  for (const remaining of [1, 0]) {
    await retries.first().click();
    await expect(retries).toHaveCount(remaining);
  }
  await expect(
    integration.getByLabel("Validation commands", { exact: true }),
  ).toBeEnabled();
  await expect(
    integration.getByLabel("Run command", { exact: true }),
  ).toBeEnabled();
});

test("a service outage blocks once, preserves drafts, and recovers without replaying a save", async ({
  page,
  request,
}, testInfo) => {
  const id = "connection-recovery";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/projects/${id}/edit/general`);
  const name = page.getByLabel("Name", { exact: true });
  const composer = page.getByRole("textbox", {
    name: "Message coordinator",
    exact: true,
  });
  await name.fill("Unsent project change");
  await composer.fill("Keep my planning draft");
  let down = false,
    saves = 0,
    probes = 0;
  await page.route("**/api/**", (route) => {
    const req = route.request();
    if (req.method() === "PUT") {
      saves++;
      down = true;
    }
    if (req.url().endsWith("/api/health")) probes++;
    // Keep the shared model-catalog fixtures active before and after the outage.
    return down ? route.abort() : route.fallback();
  });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Connection lost" });
  await expect(dialog).toBeVisible();
  await expect(
    page.locator('[data-sonner-toast][data-type="error"]'),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button")).toHaveCount(1);
  // Separate failure signals must not replace the shared scheduled retry.
  await page.evaluate(async () => {
    for (let signal = 0; signal < 6; signal++) {
      window.dispatchEvent(new Event("offline"));
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  });
  await expect.poll(() => probes).toBeGreaterThanOrEqual(2);
  expect(probes).toBeLessThan(5);
  await page.screenshot({ path: testInfo.outputPath("connection-lost.png") });
  down = false;
  await expect(dialog).toHaveCount(0);
  await expect(name).toHaveValue("Unsent project change");
  await expect(composer).toHaveValue("Keep my planning draft");
  expect(saves).toBe(1);
  expect((await (await request.get(`/api/projects/${id}`)).json()).name).toBe(
    id,
  );
  // A second outage must re-arm recovery without replacing the mounted views.
  down = true;
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await expect(dialog).toBeVisible();
  down = false;
  await dialog.getByRole("button", { name: "Try reconnecting" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(name).toHaveValue("Unsent project change");
  expect(saves).toBe(1);
});

test("boards pan from empty space while cards keep navigation", async ({
  page,
  request,
}) => {
  const id = "board-panning";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  const workspace = await projectWorkspace(request, id);
  await request.post(
    `/api/workspaces/${await projectWorkspace(request, id)}/tasks`,
    {
      data: { title: "Open this task" },
    },
  );
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/workspaces/${workspace}/tasks`);
  const board = page.locator(".board");
  await expect(page.locator(".task-card").first()).toBeVisible();
  await expect
    .poll(() => board.evaluate((el) => el.scrollWidth > el.clientWidth))
    .toBe(true);
  const blank = await board.boundingBox();
  const x = blank!.x + 120,
    y = blank!.y + blank!.height - 20;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 100, y, { steps: 12 });
  await page.mouse.up();
  await expect
    .poll(() => board.evaluate((el) => el.scrollLeft))
    .toBeGreaterThan(80);
  await page.mouse.move(x - 100, y);
  await page.mouse.down();
  await page.mouse.move(x + 20, y, { steps: 12 });
  await page.mouse.up();
  await expect.poll(() => board.evaluate((el) => el.scrollLeft)).toBe(0);
  await page.locator(".task-card").first().click();
  await expect(page).toHaveURL(/\/tasks\/T-1$/);
  await expect(
    page.getByRole("heading", { name: "Open this task" }),
  ).toBeVisible();
});
