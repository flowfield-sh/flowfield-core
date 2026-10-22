import { expect } from "@playwright/test";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
} from "./support";
import { join } from "node:path";

test("humans reorder, move, archive and restore tasks directly", async ({
  page,
  request,
}, testInfo) => {
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, "direct-planning")) },
  });
  const workspace = await projectWorkspace(request, "direct-planning");
  const base = `/workspaces/${workspace}`;
  const tasks: { id: string; key: string }[] = [];
  for (const title of ["First idea", "Second idea"]) {
    const response = await request.post(`/api${base}/tasks`, {
      data: { title },
    });
    expect(response.ok()).toBe(true);
    tasks.push(await response.json());
  }
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.goto(`${base}/tasks`);
  const backlog = page.getByRole("region", {
    name: "Backlog tasks",
    exact: true,
  });
  const next = page.getByRole("region", { name: "Up next tasks", exact: true });
  const move = (index: number) =>
    page.getByRole("button", { name: `Move ${tasks[index].key}`, exact: true });
  await move(1).focus();
  await page.keyboard.press("Alt+ArrowUp");
  await expect(backlog.locator(".task-card-link")).toHaveText([
    "Second idea",
    "First idea",
  ]);
  await move(1).focus();
  await page.keyboard.press("Alt+ArrowRight");
  await expect(next.getByRole("link", { name: /Second idea/ })).toBeVisible();
  await move(1).focus();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect(backlog.locator(".task-card-link")).toHaveText([
    "First idea",
    "Second idea",
  ]);
  await expect(move(0)).toBeEnabled();
  const start = (await move(0).boundingBox())!;
  const end = (await next.boundingBox())!;
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    start.x + start.width / 2 + 12,
    start.y + start.height / 2,
  );
  // The library retains a source placeholder and a drag preview.
  await expect
    .poll(() => backlog.locator('.card-shell[data-dragging="true"]').count())
    .toBeGreaterThan(0);
  await page.mouse.move(end.x + end.width / 2, end.y + 70, { steps: 15 });
  await expect(next).toHaveAttribute("data-drop-target", "true");
  await page.mouse.up();
  await expect(next.getByRole("link", { name: /First idea/ })).toBeVisible();
  await expect(page.locator('.card-shell[data-dragging="true"]')).toHaveCount(
    0,
  );
  await expect(next.locator(".card-shell")).toHaveCSS("visibility", "visible");
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`planning-${colorScheme}.png`),
    });
  }
  await next.getByRole("link", { name: /First idea/ }).click();
  const archive = page.getByRole("button", {
    name: "Archive task",
    exact: true,
  });
  await expect(archive).toBeEnabled();
  await archive.click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/tasks/${tasks[0].id}`)).json())
          .archived,
    )
    .toBe(true);
  await page.goto(`${base}/archive`);
  await expect(page.getByRole("link", { name: /First idea/ })).toBeVisible();
  await page.getByRole("link", { name: /First idea/ }).click();
  await page.getByRole("button", { name: "Restore task", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/tasks/${tasks[0].id}`)).json())
          .archived,
    )
    .toBe(false);
});
