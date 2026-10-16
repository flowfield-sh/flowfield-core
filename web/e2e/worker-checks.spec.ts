import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";
function seed(project: string) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/checks_browser_fixture.py"), state, project],
      { encoding: "utf8" },
    ),
  );
}

test("saved check authority invalidates on settings changes", async ({
  page,
  request,
}, info) => {
  const value = seed("check-settings");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`/workspaces/${value.workspace}/edit/workers`);
  await expect(
    page.getByRole("spinbutton", {
      name: "Maximum parallel workers",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("spinbutton", { name: "Active minutes per task" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Save task limits" }),
  ).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("workers-dark.png") });
  await page.goto(`/projects/${value.project}/edit/integration`);
  const grant = page.getByRole("checkbox", {
    name: "Let workers run checks through Flowfield",
    exact: true,
  });
  await grant.check();
  await expect(grant).toBeEnabled();
  await page.screenshot({ path: info.outputPath("settings-checks-dark.png") });
  const settings = await (
    await request.get(`/api/projects/${value.project}/integration`)
  ).json();
  expect(
    (
      await request.put(`/api/projects/${value.project}/integration`, {
        data: {
          expected_revision: settings.revision,
          target_branch: "main",
          checks: ["echo changed"],
        },
      })
    ).ok(),
  ).toBeTruthy();
  await page.reload();
  await expect(grant).not.toBeChecked();
});
