import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";
function seed(project: string) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/limits_browser_fixture.py"), state, project],
      { encoding: "utf8" },
    ),
  );
}

test("task allowance resolves Inbox immediately and retains external-response drafts", async ({
  page,
  request,
}, info) => {
  const value = seed("limit-response");
  await page.goto(`/workspaces/${value.workspace}/inbox`);
  await expect(
    page.getByRole("form", { name: "Execution allowance" }),
  ).toBeVisible();
  await page.screenshot({ path: info.outputPath("inbox-limit-light.png") });
  await page
    .getByRole("textbox", { name: "Continuation note" })
    .fill("Finish the agreed export and check the response.");
  await page
    .getByRole("button", { name: "Add allowance and continue" })
    .click();
  await expect(
    page.getByRole("form", { name: "Execution allowance" }),
  ).toHaveCount(0);
  const task = await (
    await request.get(`/api/projects/${value.project}/view/tasks/${value.task}`)
  ).json();
  expect(task.execution_limits.blocked).toBeNull();
  expect(task.status).toBe("up_next");
  const second = seed("limit-stale");
  await page.goto(`/workspaces/${second.workspace}/inbox`);
  await page
    .getByRole("textbox", { name: "Continuation note" })
    .fill("Keep my bound note.");
  const response = await request.post(
    `/api/projects/${second.project}/tasks/${second.task}/execution-limits`,
    {
      data: {
        expected_revision: second.limit.revision,
        active_seconds: 300,
        corrections: 0,
        note: "Another human extended this allowance.",
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "Add allowance and continue" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("textbox", { name: "Continuation note" }),
  ).toHaveValue("Keep my bound note.");
  await page.getByRole("button", { name: "Discard", exact: true }).click();
  await expect(
    page.getByRole("form", { name: "Execution allowance" }),
  ).toHaveCount(0);
});

test("limits share the task footer and saved check authority invalidates on settings changes", async ({
  page,
  request,
}, info) => {
  const value = seed("limit-settings");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`/projects/${value.project}/tasks/${value.task}`);
  await expect(
    page.getByRole("button", { name: "Add allowance and continue" }),
  ).toBeVisible();
  await page.screenshot({ path: info.outputPath("task-limit-dark.png") });
  await page.goto(`/workspaces/${value.workspace}/edit/workers`);
  await page
    .getByRole("spinbutton", { name: "Active minutes per task", exact: true })
    .fill("20");
  await page
    .getByRole("button", { name: "Save task limits", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Save task limits", exact: true }),
  ).toBeDisabled();
  const unchanged = await (
    await request.get(`/api/projects/${value.project}/view/tasks/${value.task}`)
  ).json();
  expect(unchanged.execution_limits.active_seconds_allowed).toBe(60);
  await page.screenshot({ path: info.outputPath("settings-limits-dark.png") });
  await page.goto(`/projects/${value.project}/edit/integration`);
  const grant = page.getByRole("checkbox", {
    name: "Allow workers to run saved project checks",
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

test("task allowance retains external-response drafts and returns after later exhaustion", async ({
  page,
  request,
}) => {
  const value = seed("limit-repeat");
  await page.goto(`/projects/${value.project}/tasks/${value.task}`);
  const note = page.getByRole("textbox", { name: "Continuation note" });
  const extend = page.getByRole("button", {
    name: "Add allowance and continue",
  });
  await note.fill("Keep this task-detail draft.");
  const response = await request.post(
    `/api/projects/${value.project}/tasks/${value.task}/execution-limits`,
    {
      data: {
        expected_revision: value.limit.revision,
        active_seconds: 300,
        corrections: 1,
        note: "Another human continued this task.",
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  await expect(extend).toBeDisabled();
  await expect(note).toHaveValue("Keep this task-detail draft.");
  await page.getByRole("button", { name: "Discard", exact: true }).click();
  await expect(note).toHaveCount(0);
  function exhaust() {
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        resolve("../tests/limits_browser_fixture.py"),
        state,
        value.project,
        "exhaust",
      ],
      { encoding: "utf8" },
    );
  }
  exhaust();
  await expect(note).toBeVisible();
  await page
    .getByRole("spinbutton", { name: "Additional minutes" })
    .fill("1440");
  await note.fill("Continue from the preserved work.");
  await extend.click();
  await expect(note).toHaveCount(0);
  exhaust();
  await expect(note).toBeVisible();
  await expect(extend).toBeDisabled();
});
