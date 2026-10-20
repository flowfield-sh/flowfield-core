import {
  fixtureProject,
  projectWorkspace,
  test,
  existingDirectory,
  state,
  connectMcp,
} from "./support";
import { join } from "node:path";
import { expect } from "@playwright/test";

test("an open board follows MCP task creation and catches up after reconnecting", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const workspace = await call("create_workspace", {
    request: {
      name: "Live task creation",
      ...Object.fromEntries(
        ["coordinator", "worker", "reviewer"].map((role) => [
          role,
          { harness: "codex", model: "test-model" },
        ]),
      ),
    },
  });
  await page.goto(`/workspaces/${workspace.id}/tasks`);
  const board = page.getByRole("region", {
    name: "Backlog tasks",
    exact: true,
  });
  await expect(board).toBeVisible();
  await expect(
    page.getByRole("status", { name: "Connected", exact: true }),
  ).toBeVisible();
  const create = (title: string) =>
    call("create_task", { workspace_id: workspace.id, task: { title } });
  await create("Created while board is open");
  await expect(
    board.getByRole("link", { name: /Created while board is open/ }),
  ).toBeVisible();
  for (let index = 0; index < 5; index++) await create(`Burst task ${index}`);
  await expect(board.getByRole("link", { name: /Burst task/ })).toHaveCount(5);

  // Hold an older board snapshot while a second creation invalidates that read.
  let captured!: () => void;
  const snapshotCaptured = new Promise<void>((resolve) => (captured = resolve));
  let release!: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  const boardUrl = `**/api/workspaces/${workspace.id}/view/board`;
  let delayed = false;
  await page.route(boardUrl, async (route) => {
    if (delayed) return route.continue();
    delayed = true;
    const response = await route.fetch();
    captured();
    await released;
    await route.fulfill({ response });
  });
  await create("Starts delayed board read");
  await snapshotCaptured;
  await create("Created during delayed read");
  await expect(
    board.getByRole("link", { name: /Created during delayed read/ }),
  ).toBeVisible();
  release();
  await page.unrouteAll({ behavior: "wait" });
  await expect(
    board.getByRole("link", { name: /Created during delayed read/ }),
  ).toBeVisible();

  // Stop only this browser's stream; the separate MCP client remains connected.
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await expect(
    page.getByRole("status", { name: "Reconnecting…", exact: true }),
  ).toBeVisible();
  await create("Created during stream outage");
  await expect(
    board.getByRole("link", { name: /Created during stream outage/ }),
  ).toHaveCount(0);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(
    board.getByRole("link", { name: /Created during stream outage/ }),
  ).toBeVisible();
});

// This asserts service-wide event traffic, so run it after parallel fixtures finish.
test("task changes refresh their workspace without reloading the workspace catalog", async ({
  page,
  request,
}) => {
  for (const id of ["scope-one", "scope-two"]) {
    expect(
      (
        await fixtureProject(request, {
          data: {
            id,
            path: existingDirectory(join(state, id)),
          },
        })
      ).ok(),
    ).toBe(true);
    expect(
      (
        await request.post(
          `/api/workspaces/${await projectWorkspace(page.request, id)}/tasks`,
          {
            data: { id: "one", title: "Original" },
          },
        )
      ).ok(),
    ).toBe(true);
  }
  const workspace = await projectWorkspace(page.request, "scope-one");
  await page.goto(`/workspaces/${workspace}/tasks`);
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: /Original/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("status", { name: "Connected", exact: true }),
  ).toBeVisible();
  // Register a second listener before writing so the test knows the event arrived.
  await page.evaluate(
    (otherWorkspace) =>
      new Promise<void>((ready) => {
        const events = new EventSource("/api/events");
        (window as unknown as { scopedChange: Promise<void> }).scopedChange =
          new Promise<void>((resolve) => {
            events.addEventListener("change", (event) => {
              if (JSON.parse(event.data).workspaces === null) ready();
              if (JSON.parse(event.data).workspaces?.includes(otherWorkspace)) {
                events.close();
                resolve();
              }
            });
          });
      }),
    await projectWorkspace(request, "scope-two"),
  );
  const reads: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "GET" &&
      ["/api/workspaces", `/api/workspaces/${workspace}/view/board`].includes(
        new URL(req.url()).pathname,
      )
    )
      reads.push(req.url());
  });
  expect(
    (
      await request.put(
        `/api/workspaces/${await projectWorkspace(page.request, "scope-two")}/tasks/one`,
        {
          data: { expected_revision: 1, title: "Other project change" },
        },
      )
    ).ok(),
  ).toBe(true);
  await page.evaluate(
    () => (window as unknown as { scopedChange: Promise<void> }).scopedChange,
  );
  expect(
    (
      await request.put(
        `/api/workspaces/${await projectWorkspace(page.request, "scope-one")}/tasks/one`,
        {
          data: { expected_revision: 1, title: "Relevant change" },
        },
      )
    ).ok(),
  ).toBe(true);
  await expect(
    page
      .locator(".workspace-resource-content")
      .getByRole("link", { name: /Relevant change/ }),
  ).toBeVisible();
  expect(reads).toHaveLength(1);
  expect(reads[0]).toContain(`${workspace}/view/board`);
});
