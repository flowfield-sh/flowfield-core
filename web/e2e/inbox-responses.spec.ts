import { execFileSync } from "node:child_process";
import { resolve, join } from "node:path";
import { expect } from "@playwright/test";
import {
  test,
  state,
  fixtureProject,
  existingDirectory,
  fixtureStages,
  projectWorkspace,
} from "./support";

function milestone(project: string) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        resolve("../tests/verification_browser_fixture.py"),
        state,
        project,
        "passed",
      ],
      { encoding: "utf8" },
    ),
  );
}

test("milestone acceptance stays in Inbox and Review column reflects the decision", async ({
  page,
  request,
}, testInfo) => {
  const value = milestone("inline-milestone");
  const base = `/workspaces/${value.workspace_id}`;
  await page.goto(`${base}/milestones`);
  await expect(
    page
      .getByRole("region", { name: "Review milestones" })
      .getByRole("link", { name: "Verify installed harness discovery" }),
  ).toBeVisible();
  await page.goto(`${base}/inbox`);
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("heading", {
      name: "Accept Verify installed harness discovery?",
    }),
  ).toBeVisible();
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await page.screenshot({
      path: testInfo.outputPath(`inbox-${width}-${theme}.png`),
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page
    .getByRole("button", { name: "Accept milestone", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Milestone accepted", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`${base}/inbox$`));
  const saved = await (
    await request.get(`/api${base}/verifications/${value.verification_id}`)
  ).json();
  expect(saved.status).toBe("accepted");
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await page
    .getByRole("link", { name: /Verify installed harness discovery/ })
    .click();
  await expect(
    page.getByRole("list", { name: "Milestone review feed" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Milestone accepted", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Acceptance criteria", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Milestone review feed" }),
  ).toHaveCount(0);
  await page.goto(`${base}/milestones`);
  await expect(
    page
      .getByRole("region", { name: "Complete milestones" })
      .getByRole("link", { name: "Verify installed harness discovery" }),
  ).toBeVisible();
});

test("Inbox drafts survive external review and guard switching views", async ({
  page,
  request,
}) => {
  const value = milestone("inline-stale");
  const base = `/workspaces/${value.workspace_id}`;
  await page.goto(`${base}/inbox`);
  await page
    .getByRole("textbox", { name: "Review note" })
    .fill("Keep my observation");
  await page.getByRole("tab", { name: "Waiting", exact: true }).click();
  await expect(
    page.getByRole("alertdialog", { name: "Discard your unsaved edits?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Keep editing" }).click();
  const current = await (
    await request.get(`/api${base}/verifications/${value.verification_id}`)
  ).json();
  expect(
    (
      await request.post(
        `/api${base}/verifications/${value.verification_id}/review`,
        { data: { expected_revision: current.revision, action: "accept" } },
      )
    ).ok(),
  ).toBe(true);
  await expect(page.getByRole("textbox", { name: "Review note" })).toHaveValue(
    "Keep my observation",
  );
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Discard note" }).click();
  await page.getByRole("tab", { name: "Waiting", exact: true }).click();
  await expect(
    page.getByRole("tabpanel", { name: "Waiting", exact: true }),
  ).toBeVisible();
});

test("task and project questions accept suggested or free answers inside Inbox", async ({
  page,
  request,
}) => {
  const project = "inline-questions";
  expect(
    (
      await fixtureProject(request, {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "INQ",
        },
      })
    ).ok(),
  ).toBe(true);
  const workspace = await projectWorkspace(request, project);
  expect(
    (
      await request.post(`/api/projects/${project}/tasks`, {
        data: {
          id: "export",
          title: "Export records",
          stages: fixtureStages(),
          status: "up_next",
        },
      })
    ).ok(),
  ).toBe(true);
  for (const [id, task] of [
    ["task", "INQ-1"],
    ["project", null],
  ]) {
    const response = await request.post(`/api/projects/${project}/questions`, {
      data: {
        id,
        task_id: task,
        question: task
          ? "Which records should be exported?"
          : "Which format should we prioritize?",
        context: "Choose the scope for the export.",
        recommendation: "Start with all records.",
        choices: ["All records", "Visible records"],
      },
    });
    expect(response.ok(), await response.text()).toBe(true);
  }
  await page.goto(`/workspaces/${workspace}/inbox`);
  const card = page.getByRole("article", {
    name: "Question: Which records should be exported?",
  });
  await expect(
    card.getByRole("heading", {
      name: "Which records should be exported?",
      exact: true,
    }),
  ).toHaveCount(1);
  await expect(
    card.getByText("Choose the scope for the export.", { exact: true }),
  ).not.toBeVisible();
  await card.getByText("Context", { exact: true }).click();
  await expect(
    card.getByText("Choose the scope for the export.", { exact: true }),
  ).toBeVisible();
  await card.getByRole("button", { name: "All records", exact: true }).click();
  await expect(
    card.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("All records");
  await card
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("All records, excluding archived entries.");
  await card.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/projects/${project}/questions/task`)
          ).json()
        ).answer,
    )
    .toBe("All records, excluding archived entries.");
  await expect(page).toHaveURL(new RegExp(`/workspaces/${workspace}/inbox$`));
  const other = page.getByRole("article", {
    name: "Question: Which format should we prioritize?",
  });
  await expect(
    other.getByRole("heading", {
      name: "Which format should we prioritize?",
      exact: true,
    }),
  ).toHaveCount(1);
  await expect(
    other.getByText("Choose the scope for the export.", { exact: true }),
  ).not.toBeVisible();
  await other
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("CSV first.");
  await other.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/projects/${project}/questions/project`)
          ).json()
        ).answer,
    )
    .toBe("CSV first.");
});

test("code review in Inbox approves the exact result and delivers without navigation", async ({
  page,
  request,
}) => {
  const project = "inline-code";
  execFileSync(resolve("../.venv/bin/python"), [
    resolve("../tests/inspection_browser_fixture.py"),
    state,
    "create",
    project,
    "INC",
  ]);
  const workspace = await projectWorkspace(request, project);
  await page.goto(`/workspaces/${workspace}/inbox`);
  await expect(
    page.getByRole("button", { name: "Approve and integrate", exact: true }),
  ).toBeEnabled({ timeout: 15000 });
  await expect(page.getByText("Destination:")).toContainText("delivery");
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await page
    .getByRole("textbox", {
      name: "Testing notes or approval comment (optional)",
    })
    .fill("Reviewed the exact candidate.");
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/projects/${project}/view/tasks/INC-1`)
          ).json()
        ).status,
      { timeout: 15000 },
    )
    .toBe("done");
  await expect(page).toHaveURL(new RegExp(`/workspaces/${workspace}/inbox$`));
});
