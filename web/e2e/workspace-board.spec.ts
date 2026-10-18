import { projectWorkspace } from "./support";
import { openResource } from "./shell-navigation";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";

test("shared workspace board, project filters, Inbox and settings", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", { data: { name: "Shared delivery" } })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  for (const [id, title] of [
    ["board-core", "Core release"],
    ["board-site", "Site release"],
  ]) {
    const path = join(state, id);
    mkdirSync(path, { recursive: true });
    expect(
      (
        await request.post(`/api${base}/projects`, {
          data: { id, path },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(
          `/api/workspaces/${await projectWorkspace(request, id)}/tasks`,
          {
            data: {
              id,
              project_id: id,
              title,

              labels: ["feature"],
            },
          },
        )
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(
          `/api/workspaces/${await projectWorkspace(request, id)}/questions`,
          {
            data: {
              id,
              task_id: id,
              question: `${title} scope?`,
              context: "Scope",
              recommendation: "Small",
            },
          },
        )
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
  await page.goto(base + "/tasks");
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: "T-1 Core release", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("work")
      .getByRole("link", { name: "T-2 Site release", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Filter by project").selectOption("board-site");
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: "T-1 Core release", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "Message coordinator", exact: true })
    .fill("Coordinate the release");
  await page
    .getByTestId("work")
    .getByRole("link", { name: "T-2 Site release", exact: true })
    .click();
  await expect(page).toHaveURL(/\/tasks\/T-2$/);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL(`${base}/tasks?project=board-site`);
  await expect(page.getByLabel("Filter by project")).toHaveValue("board-site");
  await expect(
    page.getByRole("textbox", { name: "Message coordinator", exact: true }),
  ).toHaveValue("Coordinate the release");
  await page.getByLabel("Filter by project").selectOption("");
  await expect(
    page.getByRole("button", { name: "New task", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('.card-shell[draggable="true"]')).toHaveCount(0);
  expect(
    (
      await request.post(
        `/api/workspaces/${await projectWorkspace(request, "board-site")}/tasks`,
        {
          data: { title: "New site work" },
        },
      )
    ).ok(),
  ).toBeTruthy();
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: "T-3 New site work", exact: true }),
  ).toBeVisible();
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
  await openResource(page, "Projects");
  await expect(page.getByRole("link", { name: /board-site/ })).toBeVisible();
  await openResource(page, "Settings");
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
  await openResource(page, "Tasks");
  await expect(page.getByRole("tab", { name: /^Tasks/ })).toHaveAttribute(
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
    page
      .getByTestId("work")
      .getByRole("link", { name: "T-2 Site release", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("workspace-board-narrow-dark.png"),
  });
});
