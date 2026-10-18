import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
} from "./support";

test("overflowing tabs scroll by wheel with directional fades and even gutters", async ({
  page,
  request,
}) => {
  const project = "wheel-tabs";
  await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
    },
  });
  const workspace = await projectWorkspace(request, project);
  for (let i = 0; i < 9; i++)
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
      {
        data: {
          title: `A long task title for overflow ${i}`,
        },
      },
    );
  for (let i = 1; i <= 9; i++) {
    const asked = await request.post(`/api/workspaces/${workspace}/questions`, {
      data: {
        task_id: `T-${i}`,
        question: `Confirm scope ${i}?`,
        context: "Choose the output scope.",
        recommendation: "Use the current definition.",
      },
    });
    expect(asked.ok()).toBe(true);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/workspaces/${workspace}/tasks`);
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  for (let i = 0; i < 9; i++) await work.getByRole("link").nth(i).click();
  const strip = page.locator(".workspace-tab-strip");
  const inbox = strip.getByRole("tab", { name: /^Inbox(?: \d+)?$/ });
  await inbox.click();
  await expect(strip).toHaveAttribute("data-overflow-left", "false");
  await expect(strip).toHaveAttribute("data-overflow-right", "true");
  await expect(strip).toHaveCSS("scrollbar-width", "none");
  await strip.hover();
  await page.mouse.wheel(0, 350);
  await expect
    .poll(() => strip.evaluate((node) => node.scrollLeft))
    .toBeGreaterThan(200);
  await expect(strip).toHaveAttribute("data-overflow-left", "true");
  await expect(strip).toHaveAttribute("data-overflow-right", "true");
  await page.mouse.wheel(0, 10000);
  await expect(strip).toHaveAttribute("data-overflow-right", "false");
  await page.mouse.wheel(0, -10000);
  await expect(strip).toHaveAttribute("data-overflow-left", "false");
  const spacing = await page.evaluate(() => {
    const strip = document
      .querySelector(".workspace-tab-strip")!
      .getBoundingClientRect();
    const tab = document
      .querySelector(".workspace-tab")!
      .getBoundingClientRect();
    const pane = document
      .querySelector(".workspace-resource-content")!
      .getBoundingClientRect();
    return {
      above: tab.top - strip.top,
      below: pane.top - tab.bottom,
      right: strip.right - pane.right,
    };
  });
  expect(spacing).toEqual({ above: 12, below: 12, right: 12 });
  await strip.getByRole("tab").last().focus();
  await page.keyboard.press("Enter");
  await expect(strip.getByRole("tab").last()).toBeInViewport();
  await expect(strip).toHaveAttribute("data-overflow-right", "false");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await strip.hover();
  await page.mouse.wheel(0, -10000);
  await expect(strip).toHaveAttribute("data-overflow-left", "false");
});
