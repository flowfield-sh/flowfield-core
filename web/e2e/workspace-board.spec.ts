import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("shared workspace board, project filters, milestones, Inbox and settings", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", { data: { name: "Shared delivery" } })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  const milestone = await (
    await request.post(`/api${base}/milestones`, {
      data: { title: "Launch together" },
    })
  ).json();
  for (const [id, prefix, title] of [
    ["board-core", "BCR", "Core release"],
    ["board-site", "BST", "Site release"],
  ]) {
    const path = join(state, id);
    mkdirSync(path, { recursive: true });
    expect(
      (
        await request.post(`/api${base}/projects`, {
          data: { id, path, task_prefix: prefix },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(`/api/projects/${id}/tasks`, {
          data: {
            id: "same",
            title,
            milestone_id: milestone.id,
            stages: fixtureStages(),
            labels: ["feature"],
          },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(`/api/projects/${id}/questions`, {
          data: {
            id: "same",
            question: `${title} scope?`,
            context: "Scope",
            recommendation: "Small",
          },
        })
      ).ok(),
    ).toBeTruthy();
  }
  const choice = {
    harness: "codex",
    model: "fixture-fast",
    effort: "low",
    mode: "read-only",
    fast: false,
  };
  await page.route(`**/api${base}/coordinator-settings`, (route) =>
    route.fulfill({
      json: {
        revision: 1,
        selection: choice,
        effective: {
          choice,
          source: "workspace",
          default_revision: 1,
          override_revision: null,
        },
      },
    }),
  );
  await page.route(`**/api${base}/models*`, (route) =>
    route.fulfill({
      json: [
        {
          id: choice.model,
          name: "Fixture",
          efforts: ["low"],
          modes: [{ id: "read-only", name: "Read only", description: "" }],
          fast: true,
        },
      ],
    }),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base);
  await expect(
    page.getByRole("link", { name: "BCR-1 Core release", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "BST-1 Site release", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Filter by project").selectOption("board-site");
  await expect(
    page.getByRole("link", { name: "BCR-1 Core release", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "Message coordinator", exact: true })
    .fill("Coordinate the release");
  await page
    .getByRole("link", { name: "BST-1 Site release", exact: true })
    .click();
  await expect(page).toHaveURL(/\/projects\/board-site\/tasks\/BST-1$/);
  await page
    .getByRole("button", { name: "Back to board", exact: true })
    .click();
  await expect(page).toHaveURL(base);
  await expect(page.getByLabel("Filter by project")).toHaveValue("board-site");
  await expect(
    page.getByRole("textbox", { name: "Message coordinator", exact: true }),
  ).toHaveValue("Coordinate the release");
  await page.getByLabel("Filter by project").selectOption("");
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("New site work");
  await expect(
    page.getByRole("button", { name: "Create task", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Project", { exact: true }).selectOption("board-site");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/board-site\/tasks\/BST-2$/);
  await page
    .getByRole("button", { name: "Back to board", exact: true })
    .click();
  const coreCard = page.locator(".card-shell").filter({
    has: page.getByRole("link", { name: "BCR-1 Core release", exact: true }),
  });
  const siteCard = page.locator(".card-shell").filter({
    has: page.getByRole("link", { name: "BST-1 Site release", exact: true }),
  });
  await siteCard.dragTo(coreCard, { targetPosition: { x: 20, y: 4 } });
  await expect
    .poll(async () => {
      const board = await (await request.get(`/api${base}/view/board`)).json();
      return board.tasks
        .sort(
          (a: { position: number }, b: { position: number }) =>
            a.position - b.position,
        )
        .map((task: { key: string }) => task.key);
    })
    .toEqual(["BST-1", "BCR-1", "BST-2"]);
  await page.getByRole("tab", { name: /^Milestones/ }).click();
  await expect(
    page.getByText("0 of 2 tasks done", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: /M-1 · Launch together/ }).click();
  await expect(page).toHaveURL(`${base}/milestones/M-1`);
  await expect(
    page.getByRole("dialog", { name: "Milestone details", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Fast mode", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(`${base}/milestones`);
  await page.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(
    page.getByText("Core release scope?", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Site release scope?", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Filter by project").selectOption("board-core");
  await expect(
    page.getByText("Site release scope?", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: /^Projects/ }).click();
  await expect(page.getByRole("link", { name: /board-site/ })).toBeVisible();
  await page
    .getByRole("button", { name: "Workspace settings", exact: true })
    .click();
  await page.getByLabel("Name", { exact: true }).fill("Delivery workspace");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page
      .getByRole("heading", { name: "Delivery workspace", exact: true })
      .first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("button", { name: "Fast mode", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "Workers", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Maximum parallel workers")).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("workspace-settings-desktop-light.png"),
  });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("tab", { name: /^Board/ }).click();
  await expect(page.getByRole("tab", { name: /^Board/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.getByLabel("Filter by project").selectOption("");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("workspace-board-desktop-light.png"),
  });
  await page
    .getByRole("textbox", { name: "Message coordinator", exact: true })
    .fill("");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(
    page.getByRole("link", { name: "BST-1 Site release", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("workspace-board-narrow-dark.png"),
  });
});
