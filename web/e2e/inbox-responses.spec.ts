import { execFileSync } from "node:child_process";
import { resolve, join } from "node:path";
import { expect } from "@playwright/test";
import {
  test,
  state,
  fixtureProject,
  existingDirectory,
  projectWorkspace,
  stubResourcePresence,
} from "./support";

test("mixed Inbox history opens exact permission records without response controls", async ({
  page,
  request,
}, info) => {
  await stubResourcePresence(page);
  const project = "history-records";
  const adopted = await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
    },
  });
  expect(adopted.ok(), await adopted.text()).toBeTruthy();
  const workspace = await projectWorkspace(page.request, project);
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
    task_key: "T-1",
    updated_at: now,
    state: null,
    next_action: null,
    request_href: `/workspaces/${workspace}/requests/${item.kind}/${item.id}?project_id=${project}`,
    source_href: `/workspaces/${workspace}/tasks/T-1`,
  }));
  await page.route(`**/api/workspaces/${workspace}/view/attention?*`, (route) =>
    route.fulfill({ json: { items, total: items.length, next_offset: null } }),
  );
  await page.route(`**/api/workspaces/${workspace}/requests/*/*?*`, (route) =>
    route.fulfill({
      json: items.find((item) => route.request().url().includes(item.id)),
    }),
  );
  await page.route(`**/api/workspaces/*/permissions/*?*`, (route) => {
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
    tabs.getByRole("tab", { name: "T-1 Tool permission", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(history).toBeVisible();
  await expect(page).not.toHaveURL(/permission=/);
  await tabs
    .getByRole("tab", { name: "T-1 Tool permission", exact: true })
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

test("compact Inbox opens independent question tabs and preserves their drafts", async ({
  page,
  request,
}, info) => {
  const project = "compact-questions";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, project)) },
  });
  const workspace = await projectWorkspace(request, project),
    base = `/workspaces/${workspace}`;
  await request.post(`/api${base}/tasks`, {
    data: { id: "export", title: "Export records", status: "up_next" },
  });
  for (const [id, task_id, question] of [
    ["task", "T-1", "Which records should be exported?"],
    ["project", null, "Which format should we prioritize?"],
  ]) {
    const response = await request.post(`/api${base}/questions`, {
      data: {
        id,
        task_id,
        question,
        context: "Choose the scope for the export.",
        recommendation: "Start with all records.",
        choices: ["All records", "Visible records"],
      },
    });
    expect(response.ok(), await response.text()).toBe(true);
  }
  await page.goto(base + "/inbox");
  const inbox = page.getByRole("tabpanel", { name: "Needs me", exact: true });
  const tabs = page.getByRole("tablist", { name: "Open resources" });
  const back = () => tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(2);
  await expect(inbox.getByRole("textbox")).toHaveCount(0);
  await inbox.getByRole("link", { name: /Which format/ }).click();
  await page
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("CSV first.");
  await back();
  await inbox.getByRole("link", { name: /Which records/ }).click();
  const detail = page.getByRole("region", { name: "Request", exact: true });
  await expect(
    detail.getByText("Choose the scope for the export.", { exact: true }),
  ).toBeVisible();
  await detail
    .getByRole("button", { name: "All records", exact: true })
    .click();
  await expect(
    detail.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("All records");
  await detail
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("All records, excluding archived entries.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/questions/task`)).json()).answer,
    )
    .toBe("All records, excluding archived entries.");
  await back();
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(1);
  await inbox.getByRole("link", { name: /Which format/ }).click();
  await expect(
    detail.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("CSV first.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/questions/project`)).json())
          .answer,
    )
    .toBe("CSV first.");
  await back();
  await expect(inbox.getByText("Nothing needs your attention.")).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.locator(".inbox-history-row")).toHaveCount(2);
  await page.screenshot({ path: info.outputPath("compact-history.png") });
  await page.reload();
  await expect(page.locator(".inbox-history-row")).toHaveCount(2);
  await page.goto(base + "/tasks/T-1");
  await page.locator('[data-kind="answer"] summary').click();
  await expect(
    page.getByText("All records, excluding archived entries.", { exact: true }),
  ).toBeVisible();
});

test("compact result row opens exact approval and preserves saving through an early delivery event", async ({
  page,
  request,
}, info) => {
  const project = "compact-code";
  execFileSync(resolve("../.venv/bin/python"), [
    resolve("../tests/inspection_browser_fixture.py"),
    state,
    "create",
    project,
    "CPC",
  ]);
  const workspace = await projectWorkspace(request, project),
    base = `/workspaces/${workspace}`;
  await page.goto(base + "/inbox");
  const inbox = page.getByRole("tabpanel", { name: "Needs me", exact: true });
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(1);
  await expect(
    inbox.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  await inbox.locator(".inbox-history-row").click();
  const result = page.getByRole("region", { name: "Result", exact: true });
  await expect(
    result.getByRole("button", { name: "Approve and integrate", exact: true }),
  ).toBeEnabled({ timeout: 15000 });
  await expect(result.getByText("Destination:")).toContainText("delivery");
  await result
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await result
    .getByRole("textbox", {
      name: "Testing notes or approval comment (optional)",
    })
    .fill("Reviewed the exact candidate.");
  let releaseReceipt!: () => void;
  let received = false;
  const receipt = new Promise<void>((resolve) => {
    releaseReceipt = resolve;
  });
  await page.route(
    `**/api/workspaces/${workspace}/results/*/review`,
    async (route) => {
      const response = await route.fetch();
      received = true;
      await receipt;
      await route.fulfill({ response });
    },
  );
  await result
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api${base}/view/tasks/T-1`)).json()).status,
      { timeout: 15000 },
    )
    .toBe("done");
  await expect.poll(() => received).toBe(true);
  await expect(
    result.getByRole("button", { name: "Approve and integrate", exact: true }),
  ).toHaveText("Saving…");
  releaseReceipt();
  await expect(
    result.getByRole("textbox", {
      name: "Testing notes or approval comment (optional)",
    }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  await expect(inbox.getByText("Nothing needs your attention.")).toBeVisible();
  await page.getByRole("tab", { name: "Delivered", exact: true }).click();
  const delivered = page.getByRole("region", {
    name: "Delivered tasks",
    exact: true,
  });
  await expect(delivered.locator(".inbox-history-row")).toHaveCount(1);
  await expect(delivered.getByRole("button")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("compact-delivered.png") });
  await delivered.locator(".inbox-history-row").click();
  await expect(
    result
      .locator(".entity-pane-title")
      .getByRole("img", { name: "Delivered", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".entity-pane-footer")).not.toBeVisible();
});

test("paged compact Inbox keeps independent failed-answer drafts in resource tabs", async ({
  page,
  request,
}) => {
  const project = "compact-paged";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, project)) },
  });
  const workspace = await projectWorkspace(request, project),
    base = `/workspaces/${workspace}`;
  for (let index = 0; index < 23; index++) {
    const r = await request.post(`/api${base}/questions`, {
      data: {
        id: `q-${index}`,
        question: `Paged decision ${index}`,
        context: "Choose a format.",
        recommendation: "CSV",
      },
    });
    expect(r.ok(), await r.text()).toBe(true);
  }
  await page.goto(base + "/inbox");
  const inbox = page.getByRole("tabpanel", { name: "Needs me", exact: true });
  const back = () =>
    page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  let releasePage!: () => void;
  let pageRequested!: () => void;
  const heldPage = new Promise<void>((resolve) => {
    releasePage = resolve;
  });
  const requestedPage = new Promise<void>((resolve) => {
    pageRequested = resolve;
  });
  let holdNext = true;
  await page.route(
    "**/view/attention?column=action&offset=20",
    async (route) => {
      if (holdNext) {
        holdNext = false;
        pageRequested();
        await heldPage;
      }
      await route.continue().catch(() => {});
    },
  );
  await inbox.getByRole("button", { name: "Load more", exact: true }).click();
  await requestedPage;
  // Recovery cancels the pagination request and finishes its window through
  // the base read. That window must also survive unmounting the Inbox tab.
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(23);
  releasePage();
  await inbox.getByRole("link", { name: /Paged decision 0 / }).click();
  const answer = page.getByRole("textbox", {
    name: "Your answer",
    exact: true,
  });
  await answer.fill("Keep this later-page draft.");
  await back();
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(23);
  await inbox.getByRole("link", { name: /Paged decision 22 / }).click();
  await answer.fill("CSV first.");
  await page.route(`**/api/workspaces/*/questions/q-22/answer`, (route) =>
    route.fulfill({
      status: 409,
      json: {
        error: {
          code: "revision_conflict",
          message: "Question changed; review it again.",
        },
      },
    }),
  );
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(
    page.getByText("Question changed; review it again.").first(),
  ).toBeVisible();
  await expect(answer).toHaveValue("CSV first.");
  await page.unroute(`**/api/workspaces/*/questions/q-22/answer`);
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(answer).toHaveCount(0);
  await back();
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(22);
  await inbox.getByRole("link", { name: /Paged decision 0 / }).click();
  await expect(answer).toHaveValue("Keep this later-page draft.");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(answer).toHaveCount(0);
  await back();
  await expect(inbox.locator(".inbox-history-row")).toHaveCount(21);
});
