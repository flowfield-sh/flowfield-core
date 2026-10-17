import { execFileSync } from "node:child_process";
import { resolve, join } from "node:path";
import { expect } from "@playwright/test";
import {
  test,
  state,
  fixtureProject,
  existingDirectory,
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

test("mixed Inbox history opens exact permission records without response controls", async ({
  page,
  request,
}, info) => {
  const project = "history-records";
  const adopted = await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
      task_prefix: "HIS",
    },
  });
  expect(adopted.ok(), await adopted.text()).toBeTruthy();
  const workspace = await projectWorkspace(request, project);
  const now = new Date().toISOString();
  const items = [
    {
      id: "saved-permission",
      kind: "permission",
      title: "Run isolated checks",
      status: "answered",
    },
    {
      id: "expired-permission",
      kind: "permission",
      title: "Earlier tool request",
      status: "expired",
    },
    {
      id: "saved-result",
      kind: "result",
      title: "Implement CSV export",
      status: "delivered",
    },
    {
      id: "saved-question",
      kind: "question",
      title: "Which filename should the export use?",
      status: "closed",
    },
  ].map((item) => ({
    ...item,
    project_id: project,
    task_key: "HIS-1",
    updated_at: now,
    state: null,
    next_action: null,
    request_href: `/workspaces/${workspace}/requests/${item.kind}/${item.id}?project_id=${project}`,
    source_href: `/projects/${project}/tasks/HIS-1`,
  }));
  await page.route(`**/api/workspaces/${workspace}/view/attention?*`, (route) =>
    route.fulfill({ json: { items, total: items.length, next_offset: null } }),
  );
  await page.route(`**/api/workspaces/${workspace}/requests/*/*?*`, (route) =>
    route.fulfill({
      json: items.find((item) => route.request().url().includes(item.id)),
    }),
  );
  await page.route(`**/api/projects/${project}/permissions/*?*`, (route) => {
    const expired = route.request().url().includes("expired-permission");
    return route.fulfill({
      json: {
        id: expired ? "expired-permission" : "saved-permission",
        project_id: project,
        workspace_id: null,
        role: "worker",
        task_id: "task",
        run_id: "run",
        conversation_id: null,
        binding: "test",
        turn_id: "run",
        tool_id: "tool",
        title: expired ? "Earlier tool request" : "Run isolated checks",
        details: "npm run test:isolated",
        options: [{ id: "allow", label: "Allow once", kind: "allow_once" }],
        revision: 2,
        status: expired ? "expired" : "answered",
        answer: expired ? null : "allow",
        created_at: now,
        updated_at: now,
        expires_at: now,
        released_at: expired ? null : now,
      },
    });
  });
  await page.goto(`/workspaces/${workspace}/inbox?inbox=history`);
  const history = page.getByRole("tabpanel", { name: "History", exact: true });
  for (const item of items)
    await expect(
      history.getByRole("link", {
        name: new RegExp(item.title.replace(/[?]/g, "\\?")),
      }),
    ).toBeVisible();
  await expect(
    history.getByText("npm run test:isolated", { exact: true }),
  ).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("history-light.png") });
  await history.getByRole("link", { name: /Run isolated checks/ }).click();
  const tabs = page.getByRole("tablist", { name: "Open resources" });
  await expect(
    tabs.getByRole("tab", { name: "HIS-1 Tool permission", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(history).toBeVisible();
  await expect(page).not.toHaveURL(/permission=/);
  await tabs
    .getByRole("tab", { name: "HIS-1 Tool permission", exact: true })
    .click();
  const dialog = page.getByRole("region", {
    name: "Request",
    exact: true,
  });
  await expect(
    dialog.getByText("Allow once · Answer recorded", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Allow once", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    dialog.getByText("npm run test:isolated", { exact: true }),
  ).toBeVisible();
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(page).not.toHaveURL(/permission=/);
  await history.getByRole("link", { name: /Earlier tool request/ }).click();
  await expect(
    dialog.getByText("Expired. No permission was delivered.", { exact: true }),
  ).toBeVisible();
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: info.outputPath("history-narrow-dark.png") });
});

test("milestone acceptance clears Inbox and Review column reflects the decision", async ({
  page,
  request,
}, testInfo) => {
  const value = milestone("inline-milestone");
  const base = `/workspaces/${value.workspace_id}`;
  // The HTTP receipt alone must clear the request, without relying on SSE.
  await page.route("**/api/events", (route) => route.abort());
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
  ).toHaveCount(0);
  const card = page.getByRole("article", {
    name: "Milestone acceptance: Verify installed harness discovery",
  });
  await expect(
    card.getByLabel("Response").getByRole("textbox", { name: "Review note" }),
  ).toBeVisible();
  await expect(card.locator(":scope > header").getByRole("img")).toHaveCount(1);
  await expect(
    card.getByText("Verification report", { exact: true }),
  ).toHaveCount(0);
  await expect(
    card.locator(".conversation-message, .detail-group"),
  ).toHaveCount(0);
  const headings = await card.getByRole("heading").allTextContents();
  expect(headings).toContain("Checks");
  await card.getByText("Review evidence", { exact: true }).click();
  await card.getByText("Requirements checked", { exact: true }).click();
  const revision = card.getByRole("link", { name: "revision 1", exact: true });
  await expect(revision).toHaveAttribute(
    "href",
    `${base}/milestones/${value.milestone_key}/revisions/1`,
  );
  await expect(
    card.getByRole("link", { name: /Criteria checked/ }),
  ).toHaveCount(0);
  await card.getByText("Requirements checked", { exact: true }).click();
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
  await expect(card).toHaveCount(0);
  await expect(page.getByText("Nothing needs your attention.")).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Inbox", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tabpanel", { name: "Needs me", exact: true }),
  ).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath("inbox-cleared.png") });
  await expect(page).toHaveURL(new RegExp(`${base}/inbox$`));
  const saved = await (
    await request.get(`/api${base}/verifications/${value.verification_id}`)
  ).json();
  expect(saved.status).toBe("accepted");
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await page
    .getByRole("link", { name: /Verify installed harness discovery/ })
    .click();
  const completed = page.getByRole("region", { name: "Request", exact: true });
  await expect(
    completed.getByRole("img", { name: "Milestone accepted", exact: true }),
  ).toBeVisible();
  await expect(
    completed.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
  await completed
    .locator(".editor-heading")
    .getByRole("link", { name: new RegExp(value.milestone_key + ":") })
    .click();
  await expect(
    page.getByRole("heading", { name: "Acceptance criteria", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Milestone activity" }),
  ).toHaveCount(0);
  await page.goto(`${base}/milestones`);
  await expect(
    page
      .getByRole("region", { name: "Complete milestones" })
      .getByRole("link", { name: "Verify installed harness discovery" }),
  ).toBeVisible();
});

test("Inbox drafts survive external review across view switches", async ({
  page,
  request,
}) => {
  const value = milestone("inline-stale");
  const base = `/workspaces/${value.workspace_id}`;
  await page.goto(`${base}/inbox`);
  await page
    .getByRole("textbox", { name: "Review note" })
    .fill("Keep my observation");
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
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
  await page.getByRole("tab", { name: "Needs me", exact: true }).click();
  await expect(
    page
      .getByRole("tabpanel", { name: "Needs me", exact: true })
      .getByRole("article"),
  ).toHaveCount(0);
  await expect(
    page.getByText("Unsent responses to resolved requests are saved in"),
  ).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Review note" })).toHaveValue(
    "Keep my observation",
  );
  await expect(
    page.getByRole("button", { name: "Accept milestone", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      "This request no longer needs a response. Your unsent note is preserved.",
    ),
  ).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: test.info().outputPath("external-resolution-draft.png"),
  });
  await page.getByRole("button", { name: "Discard note" }).click();
  await page.getByRole("tab", { name: "Needs me", exact: true }).click();
  await expect(
    page
      .getByRole("tabpanel", { name: "Needs me", exact: true })
      .getByRole("article"),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(
    page.getByRole("tabpanel", { name: "History", exact: true }),
  ).toBeVisible();
});

test("task and project questions accept suggested or free answers inside Inbox", async ({
  page,
  request,
}, testInfo) => {
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
  const other = page.getByRole("article", {
    name: "Question: Which format should we prioritize?",
  });
  await other
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("CSV first.");
  await expect(
    card.getByRole("heading", {
      name: "Which records should be exported?",
      exact: true,
    }),
  ).toHaveCount(1);
  await expect(
    card.getByText("Choose the scope for the export.", { exact: true }),
  ).toBeVisible();
  await expect(card.locator(":scope > header").getByRole("img")).toHaveCount(1);
  await page.screenshot({
    path: testInfo.outputPath("questions-inbox.png"),
    animations: "disabled",
  });
  await card.getByRole("button", { name: "All records", exact: true }).click();
  await expect(
    card
      .getByLabel("Response")
      .getByRole("textbox", { name: "Your answer", exact: true }),
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
  await expect(card).toHaveCount(0);
  await expect(
    other.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("CSV first.");
  await expect(
    other.getByRole("heading", {
      name: "Which format should we prioritize?",
      exact: true,
    }),
  ).toHaveCount(1);
  await expect(
    other.getByText("Choose the scope for the export.", { exact: true }),
  ).toBeVisible();
  await other
    .getByLabel("Response")
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
  await expect(other).toHaveCount(0);
  await expect(page.getByText("Nothing needs your attention.")).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(
    page.getByRole("link", { name: /Which format should we prioritize/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("link", { name: /Which format should we prioritize/ }),
  ).toBeVisible();
});

test("code review in Inbox approves the exact result and delivers without navigation", async ({
  page,
  request,
}, testInfo) => {
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
  const reviewCard = page.getByRole("article", { name: /^Result:/ });
  await expect(
    reviewCard.locator(":scope > header").getByRole("img"),
  ).toHaveCount(1);
  await expect(
    reviewCard.getByRole("heading", { name: "Checks", exact: true }),
  ).toBeVisible();
  await expect(reviewCard.locator(".detail-group")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("code-inbox.png"),
    animations: "disabled",
  });
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
  await expect(page.getByRole("article", { name: /^Result:/ })).toHaveCount(0);
  await page.goto(`/projects/${project}/tasks/INC-1`);
  await expect(
    page
      .locator(".editor-heading")
      .getByRole("img", { name: "Completed", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Outcome", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".entity-pane-footer")).toBeEmpty();
});

test("Needs me scrolls with wheel input and retains reading position during updates", async ({
  page,
  request,
}) => {
  const value = milestone("inbox-wheel");
  const base = `/workspaces/${value.workspace_id}`;
  for (let index = 0; index < 8; index++) {
    const response = await request.post(
      `/api/projects/${value.project_id}/questions`,
      {
        data: {
          question: `Decision ${index + 1}: choose the walkthrough scope`,
          context: "Confirm which setup path to cover.",
          recommendation: "Begin with a clean installation.",
          author: "coordinator",
        },
      },
    );
    expect(response.ok(), await response.text()).toBe(true);
  }
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${base}/inbox`);
    await expect(page.getByRole("article").first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Accept milestone", exact: true }),
    ).toBeEnabled();
    await expect(page.getByLabel("Your answer", { exact: true })).toHaveCount(
      width === 1440 ? 8 : 9,
    );
    await expect(
      page.getByRole("tab", { name: "Waiting", exact: true }),
    ).toHaveCount(0);
    const pane = page
      .locator(".workspace-work-content")
      .filter({ visible: true });
    const bounds = (await pane.boundingBox())!;
    await page.mouse.move(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height / 2,
    );
    // Start after all cards load; late layout/scroll restoration is not a wheel
    // gesture. Both directions below still use actual browser input.
    await pane.evaluate((n) => {
      n.scrollTop = 0;
    });
    await page.mouse.wheel(0, 600);
    await expect.poll(() => pane.evaluate((n) => n.scrollTop)).toBe(600);
    const before = await pane.evaluate((n) => n.scrollTop);
    const question = await request.post(
      `/api/projects/${value.project_id}/questions`,
      {
        data: {
          question: `Additional decision at ${width}`,
          context: "A newly discovered setup choice.",
          recommendation: "Keep the existing setup.",
          author: "coordinator",
        },
      },
    );
    expect(question.ok(), await question.text()).toBe(true);
    await expect(
      page.getByRole("heading", {
        name: `Additional decision at ${width}`,
        exact: true,
      }),
    ).toBeAttached();
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeLessThan(before + 5);
    await expect
      .poll(() => pane.evaluate((n) => n.scrollTop))
      .toBeGreaterThan(before - 5);
    await page.mouse.wheel(0, -600);
    await expect.poll(() => pane.evaluate((n) => n.scrollTop)).toBeLessThan(5);
  }
});

test("resolved requests disappear without losing later-page drafts or failed answers", async ({
  page,
  request,
}) => {
  const project = "inbox-paged";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, project)), task_prefix: "IPG" },
  });
  const workspace = await projectWorkspace(request, project);
  for (let index = 0; index < 23; index++) {
    const response = await request.post(`/api/projects/${project}/questions`, {
      data: {
        id: `q-${index}`,
        question: `Paged decision ${index}`,
        context: "Choose a format.",
        recommendation: "CSV",
      },
    });
    expect(response.ok(), await response.text()).toBe(true);
  }
  await page.goto(`/workspaces/${workspace}/inbox`);
  await page.getByRole("button", { name: "Load more", exact: true }).click();
  await expect(
    page
      .getByRole("tabpanel", { name: "Needs me", exact: true })
      .getByRole("article"),
  ).toHaveCount(23);
  const later = page.getByRole("article", {
    name: "Question: Paged decision 0",
    exact: true,
  });
  await later
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("Keep this later-page draft.");
  const first = page.getByRole("article", {
    name: "Question: Paged decision 22",
    exact: true,
  });
  await first
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("CSV first.");
  await page.route(
    `**/api/projects/${project}/questions/q-22/answer`,
    async (route) => {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "revision_conflict",
            message: "Question changed; review it again.",
          },
        }),
      });
    },
  );
  await first.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(
    page.getByText("Question changed; review it again.").first(),
  ).toBeVisible();
  await expect(
    first.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("CSV first.");
  await page.unroute(`**/api/projects/${project}/questions/q-22/answer`);
  await first.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(first).toHaveCount(0);
  await expect(
    page
      .getByRole("tabpanel", { name: "Needs me", exact: true })
      .getByRole("article"),
  ).toHaveCount(22);
  await expect(
    later.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("Keep this later-page draft.");
  await later.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(later).toHaveCount(0);
  await expect(
    page
      .getByRole("tabpanel", { name: "Needs me", exact: true })
      .getByRole("article"),
  ).toHaveCount(21);
});
