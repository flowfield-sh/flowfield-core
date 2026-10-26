import { projectWorkspace } from "./support";
import {
  fixtureMcpProject,
  test,
  existingDirectory,
  state,
  connectMcp,
} from "./support";
import { join } from "node:path";
import { expect } from "@playwright/test";

test("task links open native tabs without losing the original selection or draft", async ({
  page,
  context,
  request,
}, testInfo) => {
  // Each repetition owns its service/store through the shared fixture.
  const projectId = `native-tabs-${testInfo.repeatEachIndex}`;
  const call = await connectMcp(request);
  await fixtureMcpProject(call, {
    project_id: projectId,
    path: existingDirectory(join(state, projectId)),
  });
  const first = await call("create_task", {
    workspace_id: await projectWorkspace(request, projectId),
    task: { project_id: projectId, title: "Prepare export", status: "up_next" },
  });
  const second = await call("create_task", {
    workspace_id: await projectWorkspace(request, projectId),
    task: {
      project_id: projectId,
      title: "Download export",
      status: "up_next",
      dependencies: [first.key],
    },
  });
  const originalPath = `/workspaces/${await projectWorkspace(request, projectId)}/tasks/${second.key}`;
  await page.goto(originalPath);
  const details = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(details.getByLabel("Current task definition")).toBeVisible();
  const draft = page.getByLabel("Message coordinator", { exact: true });
  await draft.fill("Keep this requirement while inspecting the prerequisite");
  const link = details.getByRole("link", {
    name: first.key,
    exact: true,
  });
  await expect(link).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(request, projectId)}/tasks/${first.key}`,
  );
  for (const gesture of ["middle", "modifier"] as const) {
    await test.step(gesture, async () => {
      const [tab, catalog] = await Promise.all([
        context.waitForEvent("page", { timeout: 5000 }),
        context.waitForEvent("response", {
          timeout: 5000,
          predicate: (response) =>
            /^\/api\/workspaces\/[^/]+\/models$/.test(
              new URL(response.url()).pathname,
            ) && response.frame().page() !== page,
        }),
        link.click(
          gesture === "middle"
            ? { button: "middle" }
            : { modifiers: ["ControlOrMeta"] },
        ),
      ]);
      await expect(tab).toHaveURL(new RegExp(`/tasks/${first.key}$`));
      await expect(
        tab.getByRole("heading", {
          name: new RegExp(`${first.key} Draft Task$`),
        }),
      ).toBeVisible();
      await expect(
        tab.locator(".task-summary").getByRole("heading", {
          name: "Prepare export",
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        tab.getByRole("button", { name: "Back", exact: true }),
      ).toHaveCount(0);
      // Configured Coordinator views preload models. New tabs must use the
      // context-wide stub, never invoke an installed harness during this test.
      expect(catalog.frame().page()).toBe(tab);
      expect(catalog.status()).toBe(200);
      expect(await catalog.json()).toEqual([]);
      await tab.close();
      await expect(page).toHaveURL(new URL(originalPath, page.url()).href);
      await expect(draft).toHaveValue(
        "Keep this requirement while inspecting the prerequisite",
      );
    });
  }
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${first.key}$`));
  await page.goBack();
  await expect(page).toHaveURL(new URL(originalPath, page.url()).href);
});
