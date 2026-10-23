import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
} from "./support";

test("reopened browser restores tabs and history, prunes missing resources and keeps tabs through outages", async ({
  page,
  context,
  request,
}, testInfo) => {
  const projectId = `saved-tabs-${testInfo.repeatEachIndex}`;
  await fixtureProject(request, {
    data: {
      id: projectId,
      path: existingDirectory(join(state, projectId)),
    },
  });
  const workspace = await projectWorkspace(request, projectId);
  const base = `/workspaces/${workspace}`;
  await request.post(`/api${base}/tasks`, {
    data: { title: "Persistent navigation" },
  });
  await page.goto(`${base}/tasks`);
  await page
    .locator(".task-card")
    .filter({ hasText: "Persistent navigation" })
    .click();
  await expect(page).toHaveURL(`${base}/tasks/T-1`);
  const storageKey = `flowfield:tabs:${workspace}`;
  await expect
    .poll(() =>
      page.evaluate(
        (key) => JSON.parse(localStorage.getItem(key)!).tabs.length,
        storageKey,
      ),
    )
    .toBe(2);
  await page.close();
  const reopened = await context.newPage();
  await reopened.goto(`${base}/inbox`);
  await expect(reopened).toHaveURL(`${base}/tasks/T-1`);
  await expect(
    reopened.getByRole("tab", {
      name: "T-1 Persistent navigation",
      exact: true,
    }),
  ).toBeVisible();
  await reopened.getByRole("button", { name: "Back", exact: true }).click();
  await expect(reopened).toHaveURL(`${base}/tasks`);
  // Explicit deep links win over the saved active location.
  await reopened.goto(`${base}/documents`);
  await expect(reopened).toHaveURL(`${base}/documents`);
  await reopened.screenshot({ path: testInfo.outputPath("restored-tabs.png") });
  // Insert a saved absent resource, and an absent resource later removed elsewhere.
  await reopened.evaluate(
    ({ storageKey, base }) => {
      const value = JSON.parse(localStorage.getItem(storageKey)!);
      for (const key of ["T-1", "T-404"]) {
        const url = `${base}/tasks/${key}`;
        value.tabs.push({
          id: `extra-${key}`,
          root: url,
          history: [url],
          index: 0,
        });
      }
      localStorage.setItem(storageKey, JSON.stringify(value));
    },
    { storageKey, base },
  );
  await reopened.close();
  const final = await context.newPage();
  let absent = false;
  let outage = true;
  const locations = new URLSearchParams();
  for (const path of [
    "inbox",
    "tasks",
    "tasks/T-1",
    "tasks/T-404",
    "documents",
  ])
    locations.append("location", `${base}/${path}`);
  const presenceResponse = await request.get(
    `/api${base}/view/resource-presence?${locations}`,
  );
  expect(presenceResponse.ok()).toBe(true);
  const presence = await presenceResponse.json();
  // Keep the real presence response fixed while simulating outage/removal.
  // Proxy fetches can outlive route removal during the final redirect cleanup.
  await final.route("**/view/resource-presence?*", async (route) => {
    if (outage)
      return route.fulfill({
        status: 503,
        json: { detail: "Temporary failure" },
      });
    const data = { ...presence };
    if (absent) data[`${base}/tasks/T-1`] = false;
    await route.fulfill({ json: data });
  });
  await final.goto(`${base}/documents`);
  await expect(
    final.getByRole("tab", { name: "T-404", exact: true }),
  ).toBeVisible();
  outage = false;
  await final.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(
    final.getByRole("tab", { name: "T-404", exact: true }),
  ).toHaveCount(0);
  const background = final.getByRole("tab", {
    name: "T-1 Persistent navigation",
    exact: true,
  });
  await expect(background).toHaveCount(1);
  absent = true;
  await final.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(background).toHaveCount(0);
  await expect(final).toHaveURL(`${base}/documents`);
  await final.screenshot({ path: testInfo.outputPath("reconciled-tabs.png") });
  await final.emulateMedia({ colorScheme: "dark" });
  await final.setViewportSize({ width: 430, height: 900 });
  await final.screenshot({ path: testInfo.outputPath("tabs-narrow-dark.png") });
  await final.goto(`${base}/tasks/T-1`);
  await expect(final).toHaveURL(`${base}/inbox`);
  await expect(
    final.getByRole("tab", { name: "T-1 Persistent navigation", exact: true }),
  ).toHaveCount(0);
  await final.unrouteAll({ behavior: "wait" });
});
