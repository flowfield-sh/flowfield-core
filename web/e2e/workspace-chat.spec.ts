import { openResource } from "./shell-navigation";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, state, fixtureStages } from "./support";

test("member projects share coordinator history and a draft", async ({
  page,
  request,
}) => {
  async function openProject(name: string) {
    await openResource(page, "Projects");
    await page
      .locator(".project-list-row")
      .filter({ has: page.getByText(name, { exact: true }) })
      .click();
  }
  const workspace = await (
    await request.post("/api/workspaces", { data: { name: "Shared chat" } })
  ).json();
  for (const id of ["shared-core", "shared-site"]) {
    const path = join(state, id);
    mkdirSync(path, { recursive: true });
    const adopted = await request.post(
      `/api/workspaces/${workspace.id}/projects`,
      {
        data: {
          id,
          name: id,
          path,
          task_prefix: id === "shared-core" ? "COR" : "SIT",
        },
      },
    );
    expect(adopted.ok(), await adopted.text()).toBeTruthy();
    expect(
      (
        await request.post(`/api/projects/${id}/tasks`, {
          data: { id: "same", title: `${id} task`, stages: fixtureStages() },
        })
      ).ok(),
    ).toBeTruthy();
  }
  const base = `/api/workspaces/${workspace.id}/coordinator`;
  const conversation = await (await request.post(base)).json();
  await page.route(`**${base}{,?*}`, (route) =>
    route.fulfill({
      json: {
        welcome: "Plan both repositories here.",
        conversation,
        items: [],
        active: null,
        context: null,
        next_before: null,
        session_recovery_turn_id: null,
      },
    }),
  );
  const choice = {
    harness: "codex",
    model: "fixture",
    effort: "low",
    mode: "read-only",
    fast: false,
  };
  await page.route(
    `**/api/workspaces/${workspace.id}/coordinator-settings`,
    (route) =>
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
  await page.route(`**/api/workspaces/${workspace.id}/models*`, (route) =>
    route.fulfill({
      json: [
        {
          id: "fixture",
          name: "Fixture",
          efforts: ["low"],
          modes: [{ id: "read-only", name: "Read only", description: "" }],
        },
      ],
    }),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/projects/shared-core");
  await expect(
    page.getByRole("region", { name: "Coordinator conversation" }),
  ).toContainText("Plan both repositories here.");
  await page
    .getByRole("textbox", { name: "Message coordinator" })
    .fill("Keep this workspace draft");
  await openProject("shared-site");
  await expect(page).toHaveURL(/\?project=shared-site$/);
  await expect(
    page.getByRole("textbox", { name: "Message coordinator" }),
  ).toHaveValue("Keep this workspace draft");
  await expect(
    page.getByRole("region", { name: "Coordinator conversation" }),
  ).toContainText("Plan both repositories here.");
  const messages: {
    id: string;
    task_context: {
      project_id: string;
      task_id: string;
      task_revision: number;
    } | null;
    view_context: {
      surface: string;
      project_id: string | null;
      label_id: string | null;
      milestone_id: string | null;
    };
  }[] = [];
  await page.route(`**${base}/messages`, (route) => {
    messages.push(route.request().postDataJSON());
    return route.fulfill({
      status: 503,
      json: { error: { code: "fixture_unavailable", message: "Retry later" } },
    });
  });
  // Keep an uncertain send receipt, then select the same local task ID in another repo.
  // The second send must capture the new project instead of reusing the first receipt.
  await openProject("shared-core");
  await page.getByText("shared-core task", { exact: true }).first().click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(1);
  await openProject("shared-site");
  await page.getByText("shared-site task", { exact: true }).first().click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(2);
  expect(messages[0].task_context).toMatchObject({
    project_id: "shared-core",
    task_id: "same",
  });
  expect(messages[1].task_context).toMatchObject({
    project_id: "shared-site",
    task_id: "same",
  });
  expect(messages[0].id).not.toBe(messages[1].id);
  expect(messages[1].view_context).toMatchObject({
    surface: "task",
    project_id: "shared-site",
  });
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toHaveCount(0);
  const current = await (
    await request.get("/api/projects/shared-site/view/tasks/same")
  ).json();
  await request.put("/api/projects/shared-site/tasks/same", {
    data: { expected_revision: current.revision, title: "Revised site task" },
  });
  await expect(
    page.getByRole("heading", {
      name: "Revised site task",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(3);
  expect(messages[2]).toEqual(messages[1]);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page.getByLabel("Filter by project").selectOption("");
  await page.getByLabel("Filter by label").selectOption("bug");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(4);
  expect(messages[3].view_context).toMatchObject({
    surface: "board",
    project_id: null,
    label_id: "bug",
  });
  expect(messages[3].task_context).toBeNull();
  expect(messages[3].id).not.toBe(messages[2].id);
  await openResource(page, "Projects");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(5);
  expect(messages[4].view_context).toMatchObject({
    surface: "projects",
    project_id: null,
    label_id: null,
  });
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(6);
  expect(messages[5].view_context).toBeNull();
  expect(messages[5].task_context).toBeNull();
});
