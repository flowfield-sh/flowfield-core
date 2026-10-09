import { join } from "node:path";
import { expect, type Response } from "@playwright/test";
import {
  test,
  existingDirectory,
  state,
  connectMcp,
  fixtureStages,
} from "./support";

test("task links open native tabs without losing the original selection or draft", async ({
  page,
  context,
  request,
}, testInfo) => {
  // Independent records make --repeat-each useful for the native-tab regression.
  const projectId = `native-tabs-${testInfo.repeatEachIndex}`;
  const prefix = `Z${String.fromCharCode(65 + Math.floor(testInfo.repeatEachIndex / 26), 65 + (testInfo.repeatEachIndex % 26))}`;
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id: projectId,
    path: existingDirectory(join(state, projectId)),
    task_prefix: prefix,
  });
  const first = await call("create_task", {
    project_id: projectId,
    task: {
      stages: fixtureStages(),
      title: "Prepare export",
      status: "up_next",
    },
  });
  const second = await call("create_task", {
    project_id: projectId,
    task: {
      stages: fixtureStages(),
      title: "Download export",
      status: "up_next",
      dependencies: [first.key],
    },
  });
  const originalPath = `/projects/${projectId}/tasks/${second.key}`;
  await page.goto(originalPath);
  const details = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    details.getByText("Task defined", { exact: true }),
  ).toBeVisible();
  const draft = page.getByLabel("Message coordinator", { exact: true });
  await draft.fill("Keep this requirement while inspecting the prerequisite");
  const link = details.locator(".work-state").getByRole("link", {
    name: first.key,
    exact: true,
  });
  await expect(link).toHaveAttribute(
    "href",
    `/projects/${projectId}/tasks/${first.key}`,
  );
  const catalogs: Response[] = [];
  context.on("response", (response) => {
    if (new URL(response.url()).pathname === "/api/worker-models")
      catalogs.push(response);
  });

  for (const gesture of ["middle", "modifier"] as const) {
    await test.step(gesture, async () => {
      const [tab] = await Promise.all([
        context.waitForEvent("page", { timeout: 5000 }),
        link.click(
          gesture === "middle"
            ? { button: "middle" }
            : { modifiers: ["ControlOrMeta"] },
        ),
      ]);
      await expect(tab).toHaveURL(new RegExp(`/tasks/${first.key}$`));
      await expect(
        tab.getByRole("heading", {
          name: `${first.key} · Prepare export`,
          exact: true,
        }),
      ).toBeVisible();
      // Opening another tab must not start native discovery.
      expect(catalogs.filter((r) => r.frame().page() === tab)).toHaveLength(0);
      for (const response of catalogs.filter((r) => r.frame().page() === tab)) {
        expect(response.status()).toBe(200);
        expect(await response.json()).toEqual([]);
      }
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
