import {
  fixtureProject,
  projectWorkspace,
  test,
  existingDirectory,
  state,
} from "./support";
import { join } from "node:path";
import { expect } from "@playwright/test";

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
