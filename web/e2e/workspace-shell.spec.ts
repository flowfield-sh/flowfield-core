import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
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
    },
  });
  const workspace = await projectWorkspace(request, "shell-tabs");
  const base = `/workspaces/${workspace}`;
  const other = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Other shell",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  for (let i = 0; i < 14; i++)
    expect(
      (
        await request.post(
          `/api/workspaces/${await projectWorkspace(request, "shell-tabs")}/tasks`,
          {
            data: {
              title: `Standalone outcome ${i} with a long title that still stays compact`,
            },
          },
        )
      ).ok(),
    ).toBeTruthy();
  // Sidebar navigation contains actionable or running work, not planning cards.
  for (let i = 1; i <= 14; i++) {
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
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base);
  const resources = page.getByRole("navigation", {
    name: "Workspace resources",
  });
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  await expect(work.locator(".work-navigation-row")).toHaveCount(5);
  await expect(work.getByRole("link", { name: /T-14 / })).toHaveCount(1);
  await expect(work.getByRole("link", { name: /T-1 / })).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 500 });
  await work.hover();
  await page.mouse.wheel(0, 300);
  await expect
    .poll(() => work.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  await resources
    .getByRole("link", { name: "Tasks Board", exact: true })
    .click();
  await page
    .getByTestId("work")
    .getByRole("link", { name: /T-2 Standalone/ })
    .click();
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  const taskTab = page.getByRole("tab", { name: /T-2 / });
  await expect(taskTab).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Task", exact: true }).click();
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
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Content", exact: true })
    .fill("Retain this exact document draft.");
  const inactiveReads: string[] = [];
  page.on("request", (request) => {
    if (
      /\/api\/workspaces\/[^/]+\/tasks\/[^/]+\/(thread|input-eligibility)/.test(
        request.url(),
      )
    )
      inactiveReads.push(request.url());
  });
  expect(
    (
      await request.post(
        `/api/workspaces/${await projectWorkspace(request, "shell-tabs")}/tasks`,
        {
          data: {
            title: "A live update while the document is open",
          },
        },
      )
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await request.post(`/api/workspaces/${workspace}/questions`, {
        data: {
          task_id: "T-15",
          question: "Confirm the new scope?",
          context: "A live question while the document is open.",
          recommendation: "Use the current definition.",
        },
      })
    ).ok(),
  ).toBe(true);
  await expect(work.getByRole("link", { name: /T-15 / })).toBeVisible();
  expect(inactiveReads).toEqual([]);
  await taskTab.click();
  await expect(page).toHaveURL(/T-2$/);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL(/\/tasks$/);
  await page.goBack();
  await expect(page).toHaveURL(/T-2$/);
  await page
    .getByRole("tab", { name: "Workspace guidance", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("Retain this exact document draft.");
  await page
    .getByRole("button", { name: "Close Workspace guidance", exact: true })
    .click();
  await resources.getByRole("link", { name: "Documents", exact: true }).click();
  await page.getByRole("link", { name: /Workspace guidance/ }).click();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("Retain this exact document draft.");
  await page.getByLabel("Workspace", { exact: true }).selectOption(other.id);
  await expect(page.getByRole("tab", { name: /T-2 / })).toHaveCount(0);
  await page.getByLabel("Workspace", { exact: true }).selectOption(workspace);
  await page
    .getByRole("tab", { name: "Workspace guidance", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("Retain this exact document draft.");
  await page
    .getByRole("button", { name: "Save document", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Keep my original thought.");
  await expect(
    page.getByRole("button", { name: "Discuss impact" }),
  ).toHaveCount(0);
  await resources.getByRole("link", { name: "Documents", exact: true }).click();
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Keep my original thought.");
  await page.getByRole("link", { name: /Workspace guidance/ }).click();
  await page.getByLabel("Message coordinator", { exact: true }).fill("");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toBeVisible();
  await expect(taskTab).toBeVisible();
  await page.getByRole("tab", { name: /Inbox/ }).click();
  await expect(
    page.getByRole("button", { name: "Close Inbox", exact: true }),
  ).toHaveCount(0);
  const storage = await page.evaluate(() => JSON.stringify(sessionStorage));
  expect(storage).not.toContain("Retain this exact document draft");
  await taskTab.click();
  await expect(page).toHaveURL(/T-2$/);
  await expect(taskTab).toHaveCount(1);
  const comparison = page.getByRole("link", { name: "Open in work tab" });
  const popup = page.context().waitForEvent("page", { timeout: 5000 });
  await comparison.click({
    modifiers: ["ControlOrMeta"],
  });
  const nativeTab = await popup;
  await expect(nativeTab).toHaveURL(/T-2$/);
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
  await expect(page.getByRole("tooltip", { name: /T-2 / })).toBeVisible();
  await page
    .getByRole("button", { name: /^Close T-2 / })
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
  await expect(page).toHaveURL(/\/tasks$/);
  expect(errors).toEqual([]);
});
