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

test("saved checks need no permission setting", async ({
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
  await page.goto(`/projects/${value.project}/edit/validation`);
  await expect(
    page.getByRole("checkbox", {
      name: "Allow saved checks outside the worker sandbox",
    }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("Validation commands", { exact: true }),
  ).toBeVisible();
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
  await expect(
    page.getByLabel("Validation commands", { exact: true }),
  ).toHaveValue("echo changed");
});
