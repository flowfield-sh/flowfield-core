import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  projectWorkspace,
  existingDirectory,
  state,
} from "./support";

test("outer navigation reuses exact views and project overview explains its environment", async ({
  page,
  request,
}, testInfo) => {
  const project = "continuity";
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, project)),
      name: "Continuity project",
    },
  });
  const workspace = await projectWorkspace(request, project);
  const base = `/workspaces/${workspace}`;
  await page.goto(base);
  const resources = page.getByRole("navigation", {
    name: "Workspace resources",
  });
  const tabs = () =>
    page.evaluate((id) => {
      const saved = JSON.parse(localStorage.getItem(`flowfield:tabs:${id}`)!);
      return saved.tabs.map(
        (tab: { history: string[]; index: number }) => tab.history[tab.index],
      );
    }, workspace);
  await resources.getByRole("link", { name: "Settings", exact: true }).click();
  const settings = page.url().replace(new URL(page.url()).origin, "");
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  const workers = page.url().replace(new URL(page.url()).origin, "");
  await resources.getByRole("link", { name: "Settings", exact: true }).click();
  await expect.poll(tabs).toContain(workers);
  await expect.poll(tabs).toContain(settings);
  const before = await tabs();
  await resources.getByRole("link", { name: "Settings", exact: true }).click();
  expect(await tabs()).toEqual(before);
  await resources.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByRole("link", { name: /Continuity project/ }).click();
  const overview = page.getByRole("region", {
    name: "Project overview",
    exact: true,
  });
  for (const title of [
    "Directory",
    "Branch",
    "Validation",
    "Runtime",
    "Time limits",
  ])
    await expect(
      overview.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
  await expect(
    overview.getByText("Project details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    overview.getByRole("heading", { name: "Tasks", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("project-desktop.png") });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({
    path: testInfo.outputPath("project-narrow-dark.png"),
  });
  await page.getByRole("link", { name: "View tasks", exact: true }).click();
  const filtered = page.url().replace(new URL(page.url()).origin, "");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await resources
    .getByRole("link", { name: "Tasks Board", exact: true })
    .click();
  await expect.poll(tabs).toContain(filtered);
  await expect.poll(tabs).toContain(`${base}/tasks`);
  const task = await (
    await request.post(`/api/workspaces/${workspace}/tasks`, {
      data: { title: "One question" },
    })
  ).json();
  expect(
    (
      await request.post(`/api/workspaces/${workspace}/questions`, {
        data: {
          task_id: task.id,
          question: "Which scope?",
          context: "Choose scope",
          recommendation: "Small",
        },
      })
    ).ok(),
  ).toBe(true);
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await page.locator(".inbox-list .inbox-history-row").click();
  const opened = await tabs();
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await page.locator(".inbox-list .inbox-history-row").click();
  expect(await tabs()).toEqual(opened);
});
