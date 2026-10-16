import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
  fixtureStages,
} from "./support";
import { openResource } from "./shell-navigation";

test("task card entry stays on Overview and milestone status agrees across views", async ({
  page,
  request,
}) => {
  const project = "consistent-status";
  await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
      task_prefix: "WTS",
    },
  });
  const workspace = await projectWorkspace(request, project);
  const base = `/workspaces/${workspace}`;
  const milestone = await (
    await request.post(`/api${base}/milestones`, {
      data: { title: "Deliver the export" },
    })
  ).json();
  const task = await (
    await request.post(`/api/projects/${project}/tasks`, {
      data: {
        title: "Choose export format",
        milestone_id: milestone.id,
        status: "in_progress",
        stages: fixtureStages(),
      },
    })
  ).json();
  expect(
    (
      await request.post(`/api/projects/${project}/questions`, {
        data: {
          task_id: task.id,
          question: "Which format?",
          context: "Choose a format",
          recommendation: "JSON",
        },
      })
    ).ok(),
  ).toBe(true);
  await page.goto(base + "/tasks");
  const card = page.locator(".task-card").filter({ hasText: task.title });
  const status = card.getByRole("link", {
    name: "Needs your answer",
    exact: true,
  });
  await expect(status).toHaveAttribute(
    "href",
    `/projects/${project}/tasks/${task.key}`,
  );
  await status.click();
  await expect(page).toHaveURL(`/projects/${project}/tasks/${task.key}`);
  await expect(
    page.getByRole("tab", { name: "Overview", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  const row = work.getByRole("link", {
    name: `${milestone.key} ${milestone.title}`,
    exact: true,
  });
  await row.hover();
  await expect(page.getByRole("tooltip")).toContainText("Needs your answer");
  const tone = await row.locator(".work-state").getAttribute("data-tone");
  await row.click();
  const dot = page
    .locator(".editor-heading .work-identity")
    .getByRole("img", { name: "Needs your answer", exact: true });
  await expect(dot).toHaveAttribute("data-tone", tone!);
  await dot.focus();
  await expect(page.getByRole("tooltip")).toHaveText("Needs your answer");
  await openResource(page, "Milestones");
  await expect(page.locator(".milestone-board .work-state")).toHaveText(
    "Needs your answer",
  );
  const tabs = page.getByRole("tablist", { name: "Open resources" });
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  const before = await tabs.getByRole("tab").count();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(tabs.getByRole("tab")).toHaveCount(before);
  await page.getByRole("tab", { name: "Needs me", exact: true }).click();
  await page
    .getByRole("link", { name: `${task.key}: Which format?`, exact: true })
    .click();
  await expect(tabs.getByRole("tab")).toHaveCount(before + 1);
  await expect(tabs.getByRole("tab", { selected: true })).toContainText(
    task.key,
  );
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(tabs.getByRole("tab")).toHaveCount(before + 1);
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(page).toHaveURL(/\/inbox\?inbox=needs_me$/);
  const taskUrl = `/projects/${project}/tasks/${task.key}`;
  await page.evaluate(
    ({ workspace, taskUrl }) => {
      const key = `flowfield:tabs:${workspace}`;
      const saved = JSON.parse(sessionStorage.getItem(key)!);
      saved.active = saved.tabs[0].id;
      saved.tabs[0].history = [taskUrl];
      saved.tabs[0].index = 0;
      sessionStorage.setItem(key, JSON.stringify(saved));
    },
    { workspace, taskUrl },
  );
  await page.goto(taskUrl);
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(page).toHaveURL(base + "/inbox");
});

test("overflowing tabs scroll by wheel with directional fades and even gutters", async ({
  page,
  request,
}) => {
  const project = "wheel-tabs";
  await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
      task_prefix: "WHL",
    },
  });
  const workspace = await projectWorkspace(request, project);
  for (let i = 0; i < 9; i++)
    await request.post(`/api/projects/${project}/tasks`, {
      data: {
        title: `A long task title for overflow ${i}`,
        stages: fixtureStages(),
      },
    });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/workspaces/${workspace}/tasks`);
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  for (let i = 0; i < 9; i++) await work.getByRole("link").nth(i).click();
  const strip = page.locator(".workspace-tab-strip");
  const inbox = strip.getByRole("tab", { name: "Inbox", exact: true });
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
