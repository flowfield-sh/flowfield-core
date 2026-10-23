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
  const second = (await move(1).boundingBox())!;
  const first = (await backlog.locator(".card-shell").first().boundingBox())!;
  await page.mouse.move(
    second.x + second.width / 2,
    second.y + second.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    second.x + second.width / 2 + 12,
    second.y + second.height / 2,
  );
  await page.mouse.move(first.x + first.width / 2, first.y + 12, { steps: 15 });
  await expect(backlog.locator(".card-shell").first()).toHaveClass(
    /border-t-2/,
  );
  await page.mouse.up();
  await expect(backlog.locator(".task-card-link")).toHaveText([
    "Second idea",
    "First idea",
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

test("cross-column drops preserve the position between cards", async ({
  page,
  request,
}) => {
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, "planning-position")) },
  });
  const workspace = await projectWorkspace(request, "planning-position");
  const base = `/workspaces/${workspace}`;
  const keys = new Map<string, string>();
  for (const [title, status] of [
    ["Backlog one", "backlog"],
    ["Backlog two", "backlog"],
    ["Backlog three", "backlog"],
    ["Next one", "up_next"],
    ["Next two", "up_next"],
    ["Next three", "up_next"],
  ]) {
    const created = await request.post(`/api${base}/tasks`, {
      data: { title },
    });
    expect(created.ok()).toBe(true);
    const task = await created.json();
    keys.set(title, task.key);
    const moved = await request.post(
      `/api${base}/tasks/${task.id}/prioritize`,
      {
        data: {
          expected_revision: task.revision,
          status,
          before_id: null,
          author: "human",
        },
      },
    );
    expect(moved.ok()).toBe(true);
  }
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.goto(`${base}/tasks`);
  const backlog = page.getByRole("region", {
    name: "Backlog tasks",
    exact: true,
  });
  const next = page.getByRole("region", { name: "Up next tasks", exact: true });
  async function drop(title: string, column: typeof backlog, index: number) {
    const handle = page.getByRole("button", {
      name: `Move ${keys.get(title)}`,
      exact: true,
    });
    await expect(handle).toBeEnabled();
    const start = (await handle.boundingBox())!;
    const cards = column.locator(".card-shell");
    const count = await cards.count();
    const target = (await cards.nth(Math.min(index, count - 1)).boundingBox())!;
    const previous =
      index > 0 ? (await cards.nth(index - 1).boundingBox())! : null;
    const y =
      index === 0
        ? target.y + 3
        : index === count
          ? target.y + target.height + 12
          : (previous!.y + previous!.height + target.y) / 2;
    await page.mouse.move(
      start.x + start.width / 2,
      start.y + start.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      start.x + start.width / 2 + 12,
      start.y + start.height / 2,
    );
    await page.mouse.move(target.x + target.width / 2, y, { steps: 10 });
    await expect(column).toHaveAttribute("data-drop-target", "true");
    await page.mouse.up();
    await expect(column.locator(".task-card-link").nth(index)).toHaveText(
      title,
    );
    await expect(page.locator('.card-shell[data-dragging="true"]')).toHaveCount(
      0,
    );
  }
  await drop("Backlog two", next, 1);
  await expect(next.locator(".task-card-link")).toHaveText([
    "Next one",
    "Backlog two",
    "Next two",
    "Next three",
  ]);
  await drop("Next two", backlog, 1);
  await expect(backlog.locator(".task-card-link")).toHaveText([
    "Backlog one",
    "Next two",
    "Backlog three",
  ]);
  await drop("Backlog one", next, 0);
  await drop("Next three", backlog, 2);
  await page.reload();
  await expect(backlog.locator(".task-card-link")).toHaveText([
    "Next two",
    "Backlog three",
    "Next three",
  ]);
  await expect(next.locator(".task-card-link")).toHaveText([
    "Backlog one",
    "Next one",
    "Backlog two",
  ]);
});
