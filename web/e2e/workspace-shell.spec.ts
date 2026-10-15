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

test("resource tabs preserve exact history, shared drafts and workspace isolation", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await fixtureProject(request, {
    data: {
      path: existingDirectory(join(state, "shell-tabs")),
      name: "Shell tabs",
      task_prefix: "SHL",
    },
  });
  const workspace = await projectWorkspace(request, "shell-tabs");
  const base = `/workspaces/${workspace}`;
  const other = await (
    await request.post("/api/workspaces", { data: { name: "Other shell" } })
  ).json();
  await request.post(`/api${base}/milestones`, {
    data: {
      id: "outcome",
      title: "A complete outcome",
      acceptance_criteria: "Preserve the agreed requirements.",
    },
  });
  for (let i = 0; i < 14; i++)
    expect(
      (
        await request.post("/api/projects/shell-tabs/tasks", {
          data: {
            stages: fixtureStages(),
            title: `Standalone outcome ${i} with a long title that still stays compact`,
            ...(i === 0 ? { milestone_id: "outcome" } : {}),
          },
        })
      ).ok(),
    ).toBeTruthy();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base);
  const resources = page.getByRole("navigation", {
    name: "Workspace resources",
  });
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  await expect(work.getByRole("link")).toHaveCount(14);
  await expect(work.getByRole("link", { name: /SHL-1 / })).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 700 });
  await work.hover();
  await page.mouse.wheel(0, 300);
  await expect
    .poll(() => work.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  await work.getByRole("link", { name: /SHL-2 / }).click();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  const taskTab = page.getByRole("tab", { name: /SHL-2 / });
  await expect(taskTab).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  await expect(page.getByRole("list", { name: "Task feed" })).toBeVisible();
  await work.hover();
  await page.mouse.wheel(0, 300);
  await expect
    .poll(() => work.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  const workScroll = await work.evaluate((node) => node.scrollTop);
  await resources.getByRole("link", { name: "Documents", exact: true }).click();
  expect(await work.evaluate((node) => node.scrollTop)).toBe(workScroll);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("link", { name: /Workspace guidance/ }).click();
  await page
    .getByRole("button", { name: "Edit document", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Content", exact: true })
    .fill("Retain this exact document draft.");
  const inactiveReads: string[] = [];
  page.on("request", (request) => {
    if (
      /\/api\/projects\/shell-tabs\/tasks\/[^/]+\/(thread|input-eligibility)/.test(
        request.url(),
      )
    )
      inactiveReads.push(request.url());
  });
  expect(
    (
      await request.post("/api/projects/shell-tabs/tasks", {
        data: {
          title: "A live update while the document is open",
          stages: fixtureStages(),
        },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(work.getByRole("link", { name: /SHL-15 / })).toBeVisible();
  expect(inactiveReads).toEqual([]);
  await taskTab.click();
  await expect(page).toHaveURL(/SHL-2\/history$/);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL(/SHL-2$/);
  await page.goBack();
  await expect(page).toHaveURL(/SHL-2\/history$/);
  await page.getByRole("tab", { name: /^Documents/ }).click();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("Retain this exact document draft.");
  await page
    .getByRole("button", { name: "Close Documents", exact: true })
    .click();
  await resources.getByRole("link", { name: "Documents", exact: true }).click();
  await page.getByRole("link", { name: /Workspace guidance/ }).click();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("Retain this exact document draft.");
  await page.getByLabel("Workspace", { exact: true }).selectOption(other.id);
  await expect(page.getByRole("tab", { name: /SHL-2 / })).toHaveCount(0);
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
  await page.getByRole("tab", { name: /^Documents/ }).click();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("Retain this exact document draft.");
  await page
    .getByRole("button", { name: "Save document", exact: true })
    .click();
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Keep my original thought.");
  await page.getByRole("button", { name: "Discuss impact" }).click();
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Keep my original thought.");
  await page.getByRole("button", { name: "Add to message" }).click();
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue(/Keep my original thought\.[\s\S]*revision 2/);
  await page.getByLabel("Message coordinator", { exact: true }).fill("");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Edit document", exact: true }),
  ).toBeVisible();
  await expect(taskTab).toBeVisible();
  await page.getByRole("tab", { name: /Inbox/ }).click();
  await expect(
    page.getByRole("button", { name: "Close Inbox", exact: true }),
  ).toHaveCount(0);
  const storage = await page.evaluate(() => JSON.stringify(sessionStorage));
  expect(storage).not.toContain("Retain this exact document draft");
  const task = await (
    await request.get("/api/projects/shell-tabs/tasks/SHL-2")
  ).json();
  await page.goto(`/projects/shell-tabs/tasks/${task.id}/history`);
  await expect(page.getByRole("list", { name: "Task feed" })).toBeVisible();
  await expect(taskTab).toHaveCount(1);
  await work.getByRole("link", { name: /SHL-2 / }).click();
  await expect(page).toHaveURL(/SHL-2$/);
  await expect(taskTab).toHaveCount(1);
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  const comparison = page.getByRole("link", { name: "Open in work tab" });
  const popup = page.context().waitForEvent("page", { timeout: 5000 });
  await comparison.click({
    modifiers: ["ControlOrMeta"],
  });
  const nativeTab = await popup;
  await expect(nativeTab).toHaveURL(/SHL-2\/history$/);
  await nativeTab.close();
  const original = await page.evaluate((id) => {
    const state = JSON.parse(sessionStorage.getItem("flowfield:tabs:" + id)!);
    return state.tabs.find((tab: { id: string }) => tab.id === state.active);
  }, workspace);
  await comparison.click();
  await expect(taskTab).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "Back", exact: true }),
  ).toBeDisabled();
  const fresh = await page.evaluate(
    (id) => JSON.parse(sessionStorage.getItem("flowfield:tabs:" + id)!),
    workspace,
  );
  expect(
    fresh.tabs.find((tab: { id: string }) => tab.id === fresh.active).history,
  ).toEqual([original.history[original.index]]);
  expect(
    fresh.tabs.find((tab: { id: string }) => tab.id === original.id),
  ).toEqual(original);
  await page.reload();
  await expect(taskTab).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "Back", exact: true }),
  ).toBeDisabled();
  const copied = taskTab.last();
  await copied.hover();
  await expect(page.getByRole("tooltip", { name: /SHL-2 / })).toBeVisible();
  await page
    .getByRole("button", { name: /^Close SHL-2 / })
    .last()
    .hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await resources.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByRole("tab", { name: "Projects", exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await taskTab.first().click();
  await expect(
    page.getByRole("button", { name: "Back", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL(/SHL-2$/);
  expect(errors).toEqual([]);
});
