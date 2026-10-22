import { expect } from "@playwright/test";
import { test, fixtureProject, existingDirectory, state } from "./support";
import { join } from "node:path";

test("a permanently closed event stream reconnects without losing drafts", async ({
  page,
  request,
  browserName,
}) => {
  const id = `closed-stream-${browserName}`;
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  let attempts = 0;
  let recover = false;
  await page.route("**/api/events", (route) => {
    attempts++;
    return recover
      ? route.continue()
      : route.fulfill({
          status: 200,
          contentType: "text/plain",
          body: "invalid stream response",
        });
  });
  await page.goto(`/projects/${id}/edit/general`);
  const name = page
    .getByRole("region", { name: "Edit project", exact: true })
    .getByLabel("Name", { exact: true });
  await name.fill("Keep this unsaved draft");
  await expect(
    page.getByRole("status", { name: "Reconnecting…", exact: true }),
  ).toBeVisible();
  await expect.poll(() => attempts).toBeGreaterThan(1);
  recover = true;
  await expect(
    page.getByRole("status", { name: "Connected", exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await expect(name).toHaveValue("Keep this unsaved draft");
  await expect(
    page.getByRole("dialog", { name: "Connection lost" }),
  ).toHaveCount(0);
});
