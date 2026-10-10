import { fixtureProgress } from "./support";
import { fixtureStages } from "./support";
import { join } from "node:path";

import { expect } from "@playwright/test";
import {
  test,
  existingDirectory,
  state,
  cli,
  closeOverlay,
  ensureEditing,
  connectMcp,
  resultMessage,
} from "./support";

test("coordinator dependencies update blockers, links and reconciliation without browser editing", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const identity = { project_id: "dependencies" };
  await call("initialize_project", {
    ...identity,
    path: existingDirectory(join(state, "dependencies")),
  });
  const one = await call("create_task", {
    ...identity,
    task: {
      stages: fixtureStages(),
      id: "serializer",
      title: "Serialize CSV",
      body: "Deliver CSV",
      status: "up_next",
    },
  });
  const two = await call("create_task", {
    ...identity,
    task: {
      stages: fixtureStages(),
      id: "download",
      title: "Download CSV",
      body: "Offer download",
      status: "up_next",
    },
  });
  await page.goto("/projects/dependencies/tasks/DEP-2");
  const three = await call("create_task", {
    ...identity,
    task: {
      stages: fixtureStages(),
      title: "Prepare export settings",
      body: "Choose settings",
      status: "up_next",
    },
  });
  await call("edit_task", {
    ...identity,
    task_id: two.id,
    changes: {
      expected_revision: two.revision,
      dependencies: [one.id, three.id],
      stages: {
        expected_revision: 1,
        stages: fixtureStages(),
        reason: "Reconcile dependencies",
      },
    },
  });
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    detail.getByText(/^Waiting on (DEP-1, DEP-3|DEP-3, DEP-1)$/),
  ).toBeVisible();
  await expect(
    detail.getByText("Waiting on prerequisites", { exact: true }),
  ).toHaveCount(0);
  await expect(detail.locator(".task-needs")).toHaveCount(0);
  await detail.getByRole("link", { name: "DEP-3", exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText("Prepare export settings");
  await expect(detail.getByLabel("Add prerequisite")).toHaveCount(0);
  await detail
    .locator(".work-state")
    .getByRole("link", { name: "DEP-1", exact: true })
    .click();
  await expect(page).toHaveURL(/DEP-1$/);
  await page.getByText("Related work", { exact: true }).click();
  await expect(
    detail.getByRole("link", { name: "DEP-2", exact: true }),
  ).toBeVisible();
  await page.goBack();
  await fixtureProgress(request, {
    ...identity,
    task_id: one.id,
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  await expect(
    detail.getByText("Needs code from DEP-1", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("Waiting on DEP-3", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
  const card = page.locator(".task-card").filter({ hasText: "Download CSV" });
  await expect(
    card.getByText("Waiting on DEP-3", { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByRole("link", { name: "DEP-3", exact: true }),
  ).toHaveCount(1);
  await expect(
    card.getByText("Needs code from DEP-1", { exact: true }),
  ).toBeVisible();
});

test("three-letter prefix setup, coordinator dependencies and immediate accessible tooltips", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const adopted = cli([
    "project",
    "init",
    existingDirectory(join(state, "detail-settings")),
    "--name",
    "Detail settings",
    "--prefix",
    "UIX",
  ]);
  await page.goto("/projects/" + adopted.id);
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await ensureEditing(page);
  await expect(page.getByLabel("Directory", { exact: true })).toHaveCount(0);
  await page.getByLabel("Prefix", { exact: true }).fill("UIT");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeDisabled();
  await ensureEditing(page);
  await expect(page.getByLabel("Prefix", { exact: true })).toHaveValue("UIT");
  const identity = { project_id: "detail-settings" };
  const one = await call("create_task", {
    ...identity,
    task: {
      stages: fixtureStages(),
      title:
        "Build a reliable serializer with a deliberately long descriptive title",
      status: "up_next",
    },
  });
  expect(one.key).toBe("UIT-1");
  await expect(page.getByLabel("Prefix", { exact: true })).toBeDisabled();
  await page
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
  const card = page.getByRole("link", {
    name: /UIT-1 (?:Feature (?:DRAFT )?)?Build a reliable/,
  });
  await expect(card).not.toHaveAttribute("title");
  await card.hover();
  const tooltip = page.getByRole("tooltip");
  await expect(tooltip).toHaveCount(0);
  await card.focus();
  await expect(tooltip).toHaveCount(0);
  await card.click();
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const info = editor;
  await expect(info.getByLabel("Add prerequisite")).toHaveCount(0);
  await expect(
    editor.getByText("No prerequisites.", { exact: true }),
  ).toHaveCount(0);
  const prerequisite = await call("create_task", {
    ...identity,
    task: {
      stages: fixtureStages(),
      title: "Provide export fixtures",
      status: "up_next",
    },
  });
  const dependencies = editor;
  await call("edit_task", {
    ...identity,
    task_id: one.id,
    changes: {
      expected_revision: one.revision,
      dependencies: [prerequisite.id],
      stages: {
        expected_revision: 1,
        stages: fixtureStages(),
        reason: "Reconcile dependencies",
      },
      body: "Keep the agreed scope.",
    },
  });
  await expect(
    dependencies.getByText("Waiting on UIT-2", { exact: true }),
  ).toBeVisible();
  await fixtureProgress(request, {
    ...identity,
    task_id: prerequisite.key,
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  await expect(
    dependencies.getByText("Needs code from UIT-2", { exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1600, height: 1000 });

  const keyLink = dependencies
    .locator(".task-needs")
    .getByRole("link", { name: prerequisite.key, exact: true });
  await keyLink.hover();
  await expect(tooltip).toHaveText("Provide export fixtures");

  await page.setViewportSize({ width: 390, height: 844 });
  await editor.scrollIntoViewIfNeeded();
  await keyLink.hover();
  await expect(tooltip).toBeVisible();

  const mcpProject = await call("initialize_project", {
    project_id: "prefix-transport",
    path: existingDirectory(join(state, "prefix-transport")),
    task_prefix: "mcp",
  });
  expect(mcpProject.task_prefix).toBe("MCP");
  const cliProject = cli([
    "project",
    "edit",
    "--project",
    "prefix-transport",
    "--prefix",
    "CLX",
  ]);
  expect(cliProject.task_prefix).toBe("CLX");
  const reinitialized = await call("initialize_project", {
    project_id: "prefix-transport",
    path: join(state, "prefix-transport"),
    task_prefix: "clx",
  });
  expect(reinitialized.task_prefix).toBe("CLX");
});

test("task activity, Markdown and independently retrievable handoffs preserve the current agreement", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const identity = { project_id: "activity-trial" };
  await call("initialize_project", {
    ...identity,
    path: existingDirectory(join(state, "activity-trial")),
    task_prefix: "ACT",
  });
  const task = await call("create_task", {
    ...identity,
    task: {
      stages: fixtureStages(),
      title: "Download CSV",
      body: "Export filtered rows.\n\n## Done when\n\n- [ ] Preserve row order\n- [x] Quote commas",
    },
  });
  await page.goto("/projects/activity-trial/tasks/ACT-1");
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const activity = editor.getByRole("list", { name: "Task feed" });
  await expect(
    page.locator(".task-definition").getByRole("checkbox"),
  ).toHaveCount(2);
  await expect(
    editor.getByRole("button", { name: "Add note", exact: true }),
  ).toHaveCount(0);
  await expect(
    editor.getByRole("button", { name: "Add decision", exact: true }),
  ).toHaveCount(0);
  await call("add_activity", {
    ...identity,
    entry: {
      task_id: task.key,
      body: "## Finding\n\nReuse **serializer**.\n\n[Unsafe](javascript:alert(1))\n<script>window.evil = true</script>",
    },
  });
  await expect(
    activity.getByRole("heading", { name: "Finding", exact: true }),
  ).toBeVisible();
  await expect(activity.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(activity.locator("script")).toHaveCount(0);
  const old = await call("add_activity", {
    ...identity,
    entry: {
      task_id: task.key,
      kind: "handoff",
      expected_task_revision: 1,
      supersedes: null,
      body: "Visible columns only",
    },
  });
  await call("add_activity", {
    ...identity,
    entry: {
      task_id: task.key,
      kind: "handoff",
      expected_task_revision: 1,
      supersedes: old.id,
      body: "Include hidden columns but exclude secrets.",
    },
  });
  await expect(
    activity.getByRole("img", { name: "Superseded", exact: true }),
  ).toBeVisible();
  expect(
    (await call("get_task", { ...identity, task_id: task.key })).body,
  ).toBe(
    "Export filtered rows.\n\n## Done when\n\n- [ ] Preserve row order\n- [x] Quote commas",
  );
  await closeOverlay(page);
  await expect(
    page.getByRole("tab", { name: "Decisions", exact: true }),
  ).toHaveCount(0);
  const note = cli([
    "task",
    "note",
    "ACT-1",
    "--project",
    identity.project_id,
    "--body",
    "Serializer verified; download integration remains.",
  ]);
  expect(
    cli(["task", "activity", "ACT-1", "--project", identity.project_id])
      .items[0].id,
  ).toBe(note.id);
  await page.goto("/projects/activity-trial/tasks/ACT-1");
  await expect(activity).toContainText(
    "Serializer verified; download integration remains.",
  );
});

test("conversation pages exact revisions without hiding notes or superseded handoffs", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "edit-feed")),
      task_prefix: "EDF",
    },
  });
  const path = "/api/projects/edit-feed";
  await request.post(`${path}/tasks`, {
    data: {
      stages: fixtureStages(),
      id: "one",
      title: "Edit feed",
      body: "Original agreement",
    },
  });
  for (let revision = 1; revision <= 32; revision++) {
    const response = await request.put(`${path}/tasks/one`, {
      data: {
        expected_revision: revision,
        body: `Description ${revision}`,
        author: "agent",
      },
    });
    expect(response.ok()).toBe(true);
  }
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Pause to discuss scope." },
  });
  await request.put(`${path}/tasks/one`, {
    data: { expected_revision: 33, body: "After discussion", author: "agent" },
  });
  const old = await (
    await request.post(`${path}/activity`, {
      data: {
        task_id: "one",
        kind: "handoff",
        expected_task_revision: 34,
        supersedes: null,
        body: "Old handoff",
      },
    })
  ).json();
  await request.post(`${path}/activity`, {
    data: {
      task_id: "one",
      kind: "handoff",
      expected_task_revision: 34,
      supersedes: old.id,
      body: "Current handoff",
    },
  });
  await page.goto("/projects/edit-feed/tasks/EDF-1/activity");
  const activity = page.getByRole("list", { name: "Task feed" });
  await expect(activity).toContainText("Current handoff");
  await expect(
    activity.getByRole("img", { name: "Superseded", exact: true }),
  ).toBeVisible();
  await expect(activity).toContainText("Pause to discuss scope.");
  await page
    .getByRole("button", { name: "Load earlier activity", exact: true })
    .click();
  await expect(activity.locator('[data-kind="definition"]')).toHaveCount(34);
  await expect(
    page.getByRole("button", { name: "Load earlier activity", exact: true }),
  ).toHaveCount(0);
  const second = activity.locator('[data-message-id="definition:2"]');
  await second.locator(".task-changes summary").click();
  await expect
    .poll(() =>
      second.locator(".text-changes").evaluateAll((nodes) =>
        nodes
          .map((node) => {
            const copy = node.cloneNode(true) as HTMLElement;
            copy.querySelectorAll("ins, .sr-only").forEach((el) => el.remove());
            return copy.textContent;
          })
          .join(" "),
      ),
    )
    .toContain("Original agreement");
  await expect
    .poll(() =>
      second.locator(".text-changes").evaluateAll((nodes) =>
        nodes
          .map((node) => {
            const copy = node.cloneNode(true) as HTMLElement;
            copy.querySelectorAll("del, .sr-only").forEach((el) => el.remove());
            return copy.textContent;
          })
          .join(" "),
      ),
    )
    .toContain("Description 1");
});

test("question-only cards omit empty needs while project questions remain visible", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "compact-needs";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "CMP",
  });
  await call("create_task", {
    project_id,
    task: { stages: fixtureStages(), id: "one", title: "One task" },
  });
  await call("ask_question", {
    project_id,
    question: {
      id: "task-question",
      task_id: "CMP-1",
      question: "Which behavior?",
      context: "Choose the outcome",
      recommendation: "Keep it small",
      blocking_scope: "Task outcome",
    },
  });
  await page.goto(`/projects/${project_id}`);
  const card = page.locator(".task-card");
  await expect(
    card.getByRole("link", { name: "Needs your answer", exact: true }),
  ).toBeVisible();
  await expect(card.locator(".task-needs")).toHaveCount(0);
  await call("ask_question", {
    project_id,
    question: {
      id: "project-question",
      affected_task_ids: ["CMP-1"],
      question: "Which project constraint?",
      context: "Shared scope",
      recommendation: "Preserve existing behavior",
      blocking_scope: "Project constraint",
    },
  });
  await expect(
    card
      .locator(".task-needs")
      .getByRole("link", { name: "Which project constraint?", exact: true }),
  ).toBeVisible();
});

test("Needs you carries a free-text answer from browser to coordinator application", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "inbox-trial";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "ASK",
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "serializer",
      title: "Serialize CSV",
      status: "up_next",
    },
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "download",
      title: "Download CSV",
      body: "Export CSV",
      status: "up_next",
      dependencies: ["ASK-1"],
    },
  });
  const question = await call("ask_question", {
    project_id,
    question: {
      id: "export-scope",
      task_id: "ASK-2",
      question: "What should the export include?",
      context: "The table is paginated.",
      recommendation: "Export all filtered rows, capped at 10,000.",
      choices: ["All filtered rows", "Visible page"],
      blocking_scope: "Choosing the export contract",
    },
  });
  expect(cli(["inbox", "list", "--project", project_id]).needs_you_count).toBe(
    1,
  );
  await page.goto(`/projects/${project_id}`);
  // Hiding a repeated question must not leave an empty needs list on any card.
  await expect(
    page.getByRole("link", { name: "Needs your answer", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".task-card .task-needs:empty")).toHaveCount(0);
  let releaseInput!: () => void;
  const inputReady = new Promise<void>((resolve) => {
    releaseInput = resolve;
  });
  await page.route(
    `**/api/projects/${project_id}/tasks/*/input-eligibility`,
    async (route) => {
      await inputReady;
      await route.continue();
    },
  );
  await page
    .getByRole("link", { name: "Needs your answer", exact: true })
    .click();
  await expect(page).toHaveURL(/conversation\/question:export-scope:1$/);
  await expect(page.getByRole("list", { name: "Task feed" })).toBeVisible();
  const questionEntry = page.locator(
    '.conversation-message[data-kind="question"]',
  );
  await expect(questionEntry.locator(".detail-title")).toHaveText("Question");
  await expect(
    questionEntry.locator(".conversation-message-body strong"),
  ).toHaveText("What should the export include?");
  await expect(page.locator('[data-message-id="plan:1"]')).toBeAttached();
  const questionStatus = questionEntry.getByRole("img", {
    name: "Needs your answer",
  });
  await questionStatus.scrollIntoViewIfNeeded();
  // Focus after permalink positioning; Radix closes tooltips during ancestor scrolling.
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await questionStatus.focus();
  await expect(page.getByRole("tooltip")).toHaveText("Needs your answer");
  releaseInput();
  await expect(page.getByLabel("Your answer", { exact: true })).toBeEnabled();
  await expect(
    questionEntry.getByRole("img", { name: "Needs your answer" }),
  ).toBeFocused();
  await page.getByLabel("Your answer", { exact: true }).focus();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Your answer", { exact: true }),
  ).not.toBeFocused();
  await closeOverlay(page);
  await page.getByRole("tab", { name: /^Needs you(?: \d+)?$/ }).click();
  await page
    .getByRole("link", { name: /What should the export include/ })
    .click();

  await expect(
    page
      .getByRole("region", {
        name: "Needs your action",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(1);
  await expect(
    page
      .getByRole("region", {
        name: "Waiting",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", {
      name: "History",
      exact: true,
      includeHidden: true,
    }),
  ).toHaveCount(1);
  await page.reload();
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    detail
      .locator(".conversation-message-body strong")
      .filter({ hasText: "What should the export include?" }),
  ).toBeVisible();
  await expect(detail.getByRole("form", { name: "Task input" })).toContainText(
    "What should the export include?",
  );
  await expect(
    detail.getByRole("button", { name: "All filtered rows", exact: true }),
  ).toHaveCount(0);
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill(
      "All filtered rows, capped at 10,000. Explain when the limit is exceeded.",
    );
  await detail
    .getByRole("button", { name: "Back to board", exact: true })
    .click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(detail.getByLabel("Your answer", { exact: true })).toHaveValue(
    /Explain/,
  );
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(
    detail
      .locator(".conversation-message-body")
      .getByText(
        "All filtered rows, capped at 10,000. Explain when the limit is exceeded.",
        { exact: true },
      ),
  ).toBeVisible();
  await expect(detail.getByText("Answer sent.", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page
      .getByRole("region", {
        name: "Needs your action",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(0);
  await expect(
    page
      .getByRole("region", {
        name: "Waiting",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(1);
  const answered = await call("get_question", {
    project_id,
    question_id: question.id,
  });
  expect(answered.status).toBe("answered");
  expect((await call("get_task", { project_id, task_id: "ASK-2" })).body).toBe(
    "Export CSV",
  );
  expect(
    (
      await request.post(`/api/projects/${project_id}/tasks/ASK-2/progress`, {
        data: { expected_revision: 1, status: "in_progress" },
      })
    ).status(),
  ).toBe(409);

  await call("apply_answer", {
    project_id,
    question_id: question.id,
    application: {
      expected_revision: answered.revision,
      expected_task_revision: 1,
      body: "Export all filtered rows, capped at 10,000. Explain when the limit is exceeded.",
      decision:
        "Export all filtered rows with a 10,000-row cap and an explicit limit message.",
    },
  });
  await expect(detail.locator('[data-kind="activity"]')).toContainText(
    "10,000-row cap",
  );
  const applied = await call("get_task", { project_id, task_id: "ASK-2" });
  expect(applied.blocking_question_count).toBe(0);
  expect(applied.blocked_by[0].key).toBe("ASK-1");
  expect(applied.status).toBe("up_next");
  await expect(page.getByLabel("Show resolved")).toHaveCount(0);
  await expect(page.locator(".attention-card")).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await detail.scrollIntoViewIfNeeded();

  await expect(page.locator('[data-kind="activity"]')).toContainText(
    "10,000-row cap",
  );
  await expect(
    page
      .locator('[data-kind="activity"]')
      .getByRole("link", { name: "Related question", exact: true }),
  ).toHaveAttribute(
    "href",
    `/projects/${project_id}/tasks/ASK-2/conversation/question%3Aexport-scope%3A1`,
  );
});

test("Needs you preserves conflicting drafts and follows up on the same canonical question", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "inbox-conflict";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "CNF",
  });
  await call("create_task", {
    project_id,
    task: { stages: fixtureStages(), id: "one", title: "Export" },
  });
  const q = cli([
    "inbox",
    "ask",
    "--affects",
    "CNF-1",
    "--project",
    project_id,
    "--question",
    "What cap?",
    "--context",
    "Large exports need a limit.",
    "--recommendation",
    "Use 10,000 rows.",
    "--blocking-scope",
    "Export limit",
  ]);
  await page.goto(`/projects/${project_id}/inbox/${q.id}`);
  const detail = page.getByRole("dialog", { name: "Question", exact: true });
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("My unsaved custom answer");
  cli([
    "inbox",
    "answer",
    q.id,
    "--project",
    project_id,
    "--answer",
    "Cap it at 10,000.",
  ]);
  await expect(
    detail.getByText("This question changed while you were answering."),
  ).toBeVisible();
  await expect(detail.getByLabel("Your answer", { exact: true })).toHaveValue(
    "My unsaved custom answer",
  );
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("alert")).toContainText("stale");
  page.once("dialog", (d) => d.accept());
  await detail
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect(
    detail.getByText("Cap it at 10,000.", { exact: true }),
  ).toBeVisible();
  await call("follow_up_question", {
    project_id,
    question_id: q.id,
    follow_up: {
      expected_revision: 2,
      question: "What happens above the cap?",
      context: "Truncate or reject?",
      recommendation: "Reject and explain the limit.",
    },
  });
  await expect(
    detail.getByRole("heading", {
      name: "What happens above the cap?",
      exact: true,
    }),
  ).toBeVisible();
  await detail.getByText("Earlier responses", { exact: true }).click();
  await expect(detail.locator(".answer-history")).toContainText(
    "Cap it at 10,000.",
  );
  await expect(page.locator(".attention-card")).toHaveCount(1);
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("Reject, explain the cap, and suggest narrowing filters.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("status")).toContainText("Resume coordinator");
  const reply = cli(["inbox", "show", q.id, "--project", project_id]);
  expect(reply.answer).toContain("narrowing filters");
  expect(reply.revision).toBe(4);
  const withdrawal = cli([
    "inbox",
    "withdraw",
    q.id,
    "--project",
    project_id,
    "--reason",
    "Export postponed.",
  ]);
  expect(withdrawal.status).toBe("withdrawn");
  await expect(
    detail.getByRole("heading", { name: "Reason withdrawn", exact: true }),
  ).toBeVisible();
});

test("retracting answers reopens questions and preserves earlier responses", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "reversible";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "REV",
  });
  await call("create_task", {
    project_id,
    task: { stages: fixtureStages(), id: "export", title: "Export" },
  });
  await call("ask_question", {
    project_id,
    question: {
      id: "scope",
      affected_task_ids: ["REV-1"],
      question: "Which rows?",
      context: "Choose export scope",
      recommendation: "All rows",
      blocking_scope: "Export contract",
    },
  });
  await call("answer_question", {
    project_id,
    question_id: "scope",
    response: { expected_revision: 1, answer: "Visible page" },
  });
  await page.goto(`/projects/${project_id}/inbox/scope`);
  const detail = page.getByRole("dialog", { name: "Question", exact: true });
  await expect(
    detail.getByRole("link", { name: "Which rows?", exact: true }),
  ).toHaveAttribute("href", `/projects/${project_id}/inbox/scope`);
  await expect(
    detail.getByRole("button", { name: "Retract", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await request.post(
        `/api/projects/${project_id}/questions/scope/retract-answer`,
        { data: { expected_revision: 2 } },
      )
    ).ok(),
  ).toBe(true);
  await expect(detail.getByRole("status")).toHaveText("Needs your answer");
  await expect(
    page
      .getByRole("region", {
        name: "Needs your action",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(1);
  await detail.getByText("Earlier responses", { exact: true }).click();
  await expect(detail).toContainText("Visible page");
  const question = await call("get_question", {
    project_id,
    question_id: "scope",
  });
  expect(question.answer).toBeNull();
  expect(question.revision).toBe(3);
  expect(
    (await call("get_task", { project_id, task_id: "REV-1" }))
      .blocking_question_count,
  ).toBe(1);
  await detail
    .getByRole("button", { name: "Close question", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project_id}/inbox$`));
});

test("project questions link affected tasks and reconcile through CLI and MCP", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "project-input";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "PRJ",
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "first",
      title: "Catalog",
      status: "up_next",
    },
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "second",
      title: "Report",
      status: "up_next",
      dependencies: ["PRJ-1"],
    },
  });
  const q = cli([
    "inbox",
    "ask",
    "--project",
    project_id,
    "--question",
    "Which output convention?",
    "--context",
    "Both exports should agree.",
    "--recommendation",
    "UTF-8",
    "--affects",
    "PRJ-2",
    "--blocking-scope",
    "Output contract",
  ]);
  expect(q.task_id).toBeNull();
  expect(
    (await call("list_questions", { project_id, task_id: "PRJ-2" })).items[0]
      .id,
  ).toBe(q.id);
  await page.goto(`/projects/${project_id}/inbox/${q.id}`);
  const detail = page.getByRole("dialog", { name: "Question", exact: true });
  await expect(
    detail.getByRole("link", { name: "Project", exact: true }),
  ).toHaveAttribute("href", `/projects/${project_id}`);
  await expect(
    detail.getByRole("link", { name: "PRJ-2", exact: true }),
  ).toHaveAttribute("href", `/projects/${project_id}/tasks/PRJ-2`);
  await detail.getByRole("link", { name: "PRJ-2", exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText("Report");
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("UTF-8, without a byte-order mark.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("status")).toHaveText("Resume coordinator");
  const resumed = await connectMcp(request);
  const briefing = await resumed("get_board", { project_id });
  expect(briefing.recommendation.action).toBe("apply_answer");
  expect(briefing.recommendation.id).toBe(q.id);
  expect(briefing.attention.answered.items[0].id).toBe(q.id);
  const pending = await resumed("get_question", {
    project_id,
    question_id: q.id,
  });
  expect(
    (
      await request.post(
        `/api/projects/${project_id}/questions/${q.id}/retract-answer`,
        { data: { expected_revision: pending.revision } },
      )
    ).ok(),
  ).toBe(true);
  await expect(detail.getByRole("status")).toHaveText("Needs your answer");
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("UTF-8, without a byte-order mark.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("status")).toHaveText("Resume coordinator");

  const result = cli([
    "inbox",
    "apply",
    q.id,
    "--project",
    project_id,
    "--description",
    "Offline UTF-8 outputs, without BOM.",
    "--decision",
    "Exports use UTF-8 without BOM; the Report task uses the project convention.",
  ]);
  expect(result.applied_project_revision).toBe(2);
  expect(result.applied_task_revisions.second).toBe(1);
  await expect(detail.getByRole("status")).toHaveText("Answered");
  const task = await call("get_task", { project_id, task_id: "PRJ-2" });
  expect(task.blocking_question_count).toBe(0);
  expect(task.blocked_by[0].key).toBe("PRJ-1");
  const advisory = await call("ask_question", {
    project_id,
    question: {
      question: "Audience?",
      context: "Choose the project audience.",
      recommendation: "Personal use",
    },
  });
  await call("answer_question", {
    project_id,
    question_id: advisory.id,
    response: { expected_revision: 1, answer: "Personal use" },
  });
  await call("apply_answer", {
    project_id,
    question_id: advisory.id,
    application: {
      expected_revision: 2,
      expected_project_revision: 2,
      decision: "Target personal use.",
    },
  });
  await closeOverlay(page);
  expect(
    (await call("list_activity", { project_id })).items.some(
      (item: { body: string }) => item.body === "Target personal use.",
    ),
  ).toBe(true);
});

test("selected handoff survives a fresh MCP connection and shows stale context", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "handoff-trial";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "HOF",
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "catalog",
      title: "Validate catalog",
      status: "in_progress",
    },
  });
  const checkpoint = cli([
    "task",
    "handoff",
    "HOF-1",
    "--project",
    project_id,
    "--expected-revision",
    "1",
    "--supersedes",
    "none",
    "--body",
    "catalog.py validates records. Checks: 4 unit tests pass. Next: inspect duplicate-ID handling.",
    "--author",
    "agent",
  ]);
  await call("add_activity", {
    project_id,
    entry: {
      task_id: "HOF-1",
      body: "Later note, not a continuation checkpoint.",
    },
  });
  const fresh = await connectMcp(request);
  const task = await fresh("get_task", { project_id, task_id: "HOF-1" });
  expect(task.handoff.id).toBe(checkpoint.id);
  expect(task.handoff.needs_recheck).toBe(false);
  await page.goto(`/projects/${project_id}/tasks/HOF-1/activity`);
  await expect(
    page.getByRole("img", { name: "Selected handoff", exact: true }),
  ).toBeVisible();
  await call("edit_task", {
    project_id,
    task_id: "HOF-1",
    changes: { expected_revision: 1, body: "Reject duplicate IDs." },
  });
  await expect(
    page.getByRole("img", {
      name: "Task changed · Recheck before continuing",
      exact: true,
    }),
  ).toBeVisible();
  expect(
    (await fresh("get_task", { project_id, task_id: "HOF-1" })).handoff
      .needs_recheck,
  ).toBe(true);
  await call("add_activity", {
    project_id,
    entry: {
      task_id: "HOF-1",
      kind: "handoff",
      expected_task_revision: 2,
      supersedes: checkpoint.id,
      body: "Rechecked duplicate IDs; next run the full suite.",
    },
  });
  await expect(
    page.getByRole("img", { name: "Selected handoff", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Superseded", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Earlier handoff", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Replacement handoff", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("Rechecked duplicate IDs; next run the full suite.", {
      exact: true,
    }),
  ).toBeVisible();
});

test("related questions preserve the page, drafts, focus and router history", async ({
  page,
  request,
}) => {
  const project_id = "overlays";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "OVR",
    },
  });
  const call = await connectMcp(request);
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "export",
      title: "Export JSON",
      body: "Export selected books.",
      status: "up_next",
    },
  });
  const question = await call("ask_question", {
    project_id,
    question: {
      id: "scope",
      affected_task_ids: ["OVR-1"],
      question: "Which books?",
      context: "The assignment needs an export scope.",
      recommendation: "All filtered books.",
      blocking_scope: "Export scope",
    },
  });
  await page.goto(`/projects/${project_id}/tasks/OVR-1`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const editor = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const origin = `/projects/${project_id}/tasks/OVR-1`;
  await expect(
    editor.getByText("Needs your answer before work can continue:", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    editor.getByText("No dependency or input blockers.", { exact: true }),
  ).toHaveCount(0);
  const trigger = editor.locator(".task-needs").getByRole("link", {
    name: "Which books?",
    exact: true,
  });
  await page.setViewportSize({ width: 1280, height: 300 });
  await trigger.scrollIntoViewIfNeeded();
  const taskDialog = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  const overlayScroll = await taskDialog
    .locator(".entity-overlay-body")
    .evaluate((el) => el.scrollTop);
  expect(overlayScroll).toBeGreaterThan(0);
  const scroll = await page.evaluate(() => scrollY);
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Question", exact: true });
  await expect(dialog).toBeVisible();
  expect(await page.evaluate(() => scrollY)).toBe(scroll);
  await expect(page).toHaveURL(`${origin}/related/questions/${question.id}`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  const answer = dialog.getByLabel("Your answer", { exact: true });
  await answer.fill("My unsaved answer");
  await page.goBack();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(answer).toHaveValue("My unsaved answer");
  await dialog
    .getByRole("button", { name: "Close question", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(origin);
  await expect(trigger).toBeFocused();
  await expect
    .poll(() =>
      taskDialog.locator(".entity-overlay-body").evaluate((el) => el.scrollTop),
    )
    .toBe(overlayScroll);
  await trigger.click();
  await page.goBack();
  await expect(dialog).toHaveCount(0);
  await page.goForward();
  await expect(dialog).toBeVisible();
  await page.reload();
  await expect(dialog).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });

  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(origin);
  await page.goto(`${origin}/related/questions/${question.id}`);
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Your answer", { exact: true }).fill("All books.");
  await dialog
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toHaveText("Resume coordinator");
  await dialog
    .getByRole("button", { name: "Close question", exact: true })
    .click();
  await expect(page).toHaveURL(origin);
  await expect(dialog).toHaveCount(0);
  await expect(
    editor.getByText("Ask the coordinator to apply your answer:", {
      exact: true,
    }),
  ).toBeVisible();
  await closeOverlay(page);
  await page.getByRole("tab", { name: /^Needs you(?: \d+)?$/ }).click();
  for (const name of ["Needs your action", "Waiting", "History"])
    await expect(page.getByRole("region", { name, exact: true })).toBeVisible();
  await expect(page.getByLabel("Show resolved")).toHaveCount(0);
});

test("review journey preserves feedback, navigates complete files and reviews a successor from Needs you", async ({
  page,
  request,
}) => {
  const project_id = "review-flow";
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "RFL",
  });
  await call("create_task", {
    project_id,
    task: { stages: fixtureStages(), id: "one", title: "Validate CSV records" },
  });
  await call("create_task", {
    project_id,
    task: {
      stages: fixtureStages(),
      id: "two",
      title: "Document import options",
    },
  });
  await call("ask_question", {
    project_id,
    question: {
      id: "scope",
      task_id: "RFL-2",
      question: "Which import options should we document?",
      context: "Choose the first release scope.",
      recommendation: "CSV only.",
      blocking_scope: "Documentation scope",
    },
  });
  const baseRun = {
    project_id,
    task_id: "one",
    task_key: "RFL-1",
    revision: 7,
    model: "fixture-model",
    effort: "low",
    created_at: "2026-01-01T10:00:00Z",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:01:00Z",
    base_commit: "a".repeat(40),
    result_commit: "b".repeat(40),
    predecessor_id: null as string | null,
    result: {
      summary: "Validate every CSV row before importing.",
      checks: "12 deterministic checks passed.",
      limitations: "Large CSVs are not streamed yet.",
    },
    feedback: "",
    problem: null as string | null,
    code_available: false,
    usage: { total_tokens: 100, cached_input_tokens: 50, complete: true },
  };
  let first = { ...baseRun, id: "first", status: "in_review" };
  const older = {
    ...baseRun,
    id: "older",
    status: "changes_requested",
    feedback: "Validate the input rows.",
    result_commit: "c".repeat(40),
  };
  const failure = {
    ...baseRun,
    id: "failed",
    task_id: "two",
    task_key: "RFL-2",
    status: "failed",
    result: null,
    result_commit: null,
    problem: "The check command exited before a result was captured.",
  };
  let successor: typeof first | null = null;
  let failMoreFiles = true;
  let failFilePreview = false;
  const files = [
    {
      id: 0,
      old_path: "loader.py",
      new_path: "loader.py",
      change: "modified",
      old_mode: "100644",
      new_mode: "100644",
    },
    {
      id: 1,
      old_path: null,
      new_path: "tests/test_loader.py",
      change: "added",
      old_mode: "000000",
      new_mode: "100644",
    },
    {
      id: 2,
      old_path: null,
      new_path: "fixture.bin",
      change: "added",
      old_mode: "000000",
      new_mode: "100644",
    },
  ];
  const version = (run: typeof first | typeof failure) => ({
    id: run.id,
    project_id: run.project_id,
    task_id: run.task_id,
    task_key: run.task_key,
    version: run.id === "older" ? 1 : run.id === "first" ? 2 : 3,
    revision: run.revision,
    run_id: run.id,
    completion: "code",
    source_commit: run.result_commit,
    candidate_commit: run.result_commit,
    report: run.result,
    status:
      run.status === "in_review"
        ? "ready"
        : run.status === "accepted"
          ? "delivered"
          : run.status,
    created_at: run.created_at,
    target_branch: "integration",
    integration_id: run.id,
    feedback: run.status === "changes_requested" ? run.feedback : "",
    problem: run.problem,
    next_action:
      run.status === "changes_requested"
        ? {
            owner: "worker_queue",
            action: "wait",
            label: "Waiting for capacity",
            reason:
              "The service will start the requested follow-up when capacity is available.",
          }
        : null,
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route(
    `**/api/projects/${project_id}/view/attention**`,
    async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      const column = new URL(route.request().url()).searchParams.get("column");
      const runs =
        column === "action"
          ? [
              failure,
              ...(successor
                ? successor.status === "in_review"
                  ? [successor]
                  : []
                : [first]),
            ]
          : column === "history"
            ? [
                older,
                ...(successor ? [first] : []),
                ...(successor?.status === "accepted" ? [successor] : []),
              ]
            : [];
      const items = [
        ...data.items,
        ...runs.map((run) => ({
          id: run.id,
          kind: run.status === "failed" ? "intervention" : "result",
          task_key: run.task_key,
          title:
            run.task_id === "two"
              ? "Document import options"
              : "Validate CSV records",
          status: run.status === "in_review" ? "ready" : run.status,
          updated_at: run.created_at,
          code_available: run.code_available,
        })),
      ];
      await route.fulfill({
        json: { items, total: items.length, next_offset: null },
      });
    },
  );
  await page.route(`**/api/projects/${project_id}/**`, async (route) => {
    const url = new URL(route.request().url());
    const segments = url.pathname.split("/");
    const id = segments[5];
    if (url.pathname.endsWith("/thread"))
      return route.fulfill({
        json: {
          items: [
            ...(successor ? [resultMessage(version(successor))] : []),
            resultMessage(version(first)),
            resultMessage(version(older)),
          ],
          next_cursor: null,
        },
      });
    if (url.pathname.includes("/thread/result")) {
      const key = decodeURIComponent(segments.at(-1)!).split(":")[1];
      return route.fulfill({
        json: resultMessage(
          version(
            key === "older" ? older : key === "successor" ? successor! : first,
          ),
        ),
      });
    }
    if (url.pathname.endsWith("/input-eligibility"))
      return route.fulfill({
        json: {
          enabled: true,
          reason: "idle",
          task_revision: 1,
          agreement_revision: 1,
          result_id: (successor ?? first).id,
          result_revision: (successor ?? first).revision,
          question_id: null,
          question_revision: null,
          run_id: null,
          pending_reply_id: null,
        },
      });

    const run =
      id === "successor"
        ? successor!
        : id === "older"
          ? older
          : id === "failed"
            ? failure
            : first;
    if (url.pathname.endsWith("/tasks/one/results"))
      return route.fulfill({
        json: {
          items: [
            ...(successor ? [version(successor)] : []),
            version(first),
            version(older),
          ],
          current_id: (successor ?? first).id,
          current_run_id: (successor ?? first).id,
          next_before: null,
        },
      });
    if (url.pathname.endsWith("/review") || url.pathname.endsWith("/replies")) {
      const body = route.request().postDataJSON();
      if (body.action === "changes") {
        expect(body.binding.result_id).toBe(first.id);
        expect(body.binding.result_revision).toBe(first.revision);
        first = {
          ...first,
          revision: first.revision + 1,
          status: "changes_requested",
          feedback: body.body,
        };
        successor = {
          ...baseRun,
          id: "successor",
          status: "in_review",
          predecessor_id: first.id,
          base_commit: first.result_commit,
          result_commit: "d".repeat(40),
          feedback: body.body,
        };
        return route.fulfill({ json: version(first) });
      }
      successor = { ...successor!, status: "accepted", revision: 8 };
      return route.fulfill({ json: version(successor) });
    }
    if (url.pathname.endsWith("/diff")) {
      if (url.searchParams.has("offset") && failMoreFiles) {
        failMoreFiles = false;
        failFilePreview = true;
        return route.fulfill({
          status: 503,
          json: { error: { message: "More files temporarily unavailable" } },
        });
      }
      return route.fulfill({
        json: {
          files: url.searchParams.has("offset")
            ? files.slice(2)
            : files.slice(0, 2),
          total_files: 3,
          next_offset: url.searchParams.has("offset") ? null : 2,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    }
    if (url.pathname.includes("/diff/")) {
      const file = files[Number(segments.at(-1))];
      if (file.id === 1 && failFilePreview) {
        failFilePreview = false;
        return route.fulfill({
          status: 503,
          json: { error: { message: "File preview temporarily unavailable" } },
        });
      }
      const text =
        file.id === 0
          ? "diff --git a/loader.py b/loader.py\n--- a/loader.py\n+++ b/loader.py\n@@ -1 +1,160 @@\n-value = 1\n" +
            Array.from({ length: 160 }, (_, i) => `+value_${i} = ${i}\n`).join(
              "",
            )
          : "diff --git a/tests/test_loader.py b/tests/test_loader.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_loader.py\n@@ -0,0 +1 @@\n+assert validate('row')\n";
      return route.fulfill({
        json: {
          file,
          text: file.id === 2 ? null : text,
          binary: file.id === 2,
          omitted_reason:
            file.id === 2 ? "Binary file. No text preview is available." : null,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    }
    if (url.pathname.endsWith("/location"))
      return route.fulfill({
        json: {
          workspace: "/fixture/worktree",
          diff_command: "git diff BASE RESULT",
          try_command: "Run the reported checks",
        },
      });
    if (url.pathname.endsWith("/runs"))
      return route.fulfill({
        json: {
          items: url.searchParams.has("attention")
            ? [
                ...(successor?.status === "in_review"
                  ? [successor]
                  : first.status === "in_review"
                    ? [first]
                    : []),
                failure,
              ]
            : [...(successor ? [successor] : []), first, older],
          next_before: null,
        },
      });
    if (url.pathname.includes("/integrations/"))
      return route.fulfill({ json: { id: run.id, checks: [] } });
    if (url.pathname.includes("/results/"))
      return route.fulfill({ json: version(run) });
    if (url.pathname.includes("/runs/")) return route.fulfill({ json: run });
    return route.fallback();
  });
  // Result explanations use the same observed state even when a separate settings read fails.
  const workerSettingsPath = `**/api/projects/${project_id}/workers`;
  await page.route(workerSettingsPath, (route) => route.abort());
  first = { ...first, status: "changes_requested" };
  await page.goto(`/projects/${project_id}/tasks/RFL-1/changes/first`);
  await expect(
    page.getByRole("region", { name: "Proposed result", exact: true }).first(),
  ).toContainText(
    "The service will start the requested follow-up when capacity is available.",
  );
  await expect(page.locator("body")).not.toContainText(
    "The worker queue is paused",
  );
  await page.unroute(workerSettingsPath);
  first = { ...first, status: "in_review" };
  await page.goto(`/projects/${project_id}/inbox`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  await expect(
    page.getByRole("link", {
      name: /Changes RFL-1 Validate CSV records Review changes/,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: /Intervention RFL-2 Document import options/,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Question RFL-2 Which import options/ }),
  ).toBeVisible();

  await page
    .getByRole("link", {
      name: /Changes RFL-1 Validate CSV records Review changes/,
    })
    .click();
  await expect(page).toHaveURL(/changes\/first$/);
  const runs = page
    .getByRole("region", { name: "Proposed result", exact: true })
    .filter({ has: page.getByText("Ready for review", { exact: true }) });
  const checks = runs.getByRole("region", { name: "Checks", exact: true });
  await checks.getByText("Worker’s test report", { exact: true }).click();
  await expect(checks).toContainText("12 deterministic checks passed.");
  await checks
    .getByRole("button", { name: "About these checks" })
    .last()
    .scrollIntoViewIfNeeded();
  await checks
    .getByRole("button", { name: "About these checks" })
    .last()
    .evaluate(async (button) => {
      // Radix dismisses tooltips on scroll. Let the explicit scroll finish before
      // testing focus, and do not ask focus to scroll the feed a second time.
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
      button.focus({ preventScroll: true });
    });
  await expect(page.getByRole("tooltip")).toContainText(
    "worker’s account of testing in its own checkout",
  );
  await page.keyboard.press("Escape");
  await expect(checks).toBeVisible();
  await expect(page.getByText("Ready for review", { exact: true })).toHaveCount(
    1,
  );
  await expect(
    runs.getByRole("button", { name: "Withdraw review", exact: true }),
  ).toHaveCount(0);
  await runs.getByText("Code changes", { exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toHaveCount(160);
  await expect(runs.locator(".diff-gutter").first()).toBeVisible();
  await expect(runs.locator(".token.number").first()).toBeVisible();
  await runs.getByRole("button", { name: "Split", exact: true }).click();
  await expect(runs.locator(".diff-split")).toBeVisible();

  const dialog = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });

  await runs.locator(".file-preview").scrollIntoViewIfNeeded();

  await runs.getByRole("button", { name: "More files", exact: true }).click();
  await expect(runs).toContainText("More files temporarily unavailable");
  await runs
    .getByRole("button", { name: "Added tests/test_loader.py", exact: true })
    .click();
  await expect(runs).toContainText("File preview temporarily unavailable");
  await runs.getByRole("button", { name: "Retry file", exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "assert validate",
  );
  await expect(runs).not.toContainText("More files temporarily unavailable");
  await runs.getByRole("button", { name: "More files", exact: true }).click();
  await runs
    .getByRole("button", { name: "Added fixture.bin", exact: true })
    .click();
  await expect(runs).toContainText("No text preview is available.");
  await runs
    .getByRole("button", { name: "Added tests/test_loader.py", exact: true })
    .click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "assert validate",
  );
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await page
    .getByLabel("Feedback for this result")
    .fill("Include the source path in validation errors.");
  first = { ...first, revision: first.revision + 1 };
  await request.post(`/api/projects/${project_id}/activity`, {
    data: {
      id: "refresh-review",
      task_id: "one",
      kind: "note",
      body: "Refresh the review fixture.",
      author: "fixture",
    },
  });
  await expect(
    page.getByRole("button", { name: "Send feedback", exact: true }),
  ).toBeDisabled();
  await expect(
    page
      .getByRole("region", { name: "Task details", exact: true })
      .getByRole("status"),
  ).toContainText("The context changed");
  await page
    .getByRole("button", { name: "Use current context", exact: true })
    .click();
  await page
    .locator('[data-message-id="result:older"]')
    .getByRole("link", { name: "Inspect earlier result", exact: true })
    .click();
  await expect(page.locator('[data-message-id="result:older"]')).toContainText(
    "Validate the input rows.",
  );
  await expect(page.getByLabel("Feedback for this result")).toHaveValue(
    "Include the source path in validation errors.",
  );
  await expect(
    runs.getByRole("button", {
      name: "Added tests/test_loader.py",
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Feedback for this result")).toHaveValue(
    "Include the source path in validation errors.",
  );
  await page
    .getByRole("button", { name: "Send feedback", exact: true })
    .click();
  await expect(page.locator('[data-message-id="result:first"]')).toContainText(
    "Review request",
  );
  await page.reload();
  await runs
    .getByText("Requested in the previous review", { exact: true })
    .click();
  await expect(
    runs.getByText("This revision responds to the request below."),
  ).toBeVisible();
  await expect(
    runs.getByText("Include the source path in validation errors.", {
      exact: true,
    }),
  ).toBeVisible();
  await runs.getByText("Code changes", { exact: true }).click();
  await runs.getByRole("button", { name: "Unified", exact: true }).click();

  await page.setViewportSize({ width: 390, height: 844 });

  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.getByLabel("Testing notes or approval comment (optional)", {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.locator('[data-message-id="result:successor"]'),
  ).toContainText("Task complete.");
  await dialog
    .getByRole("button", {
      name: /^(Close editor|Back to board)$/,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(`/projects/${project_id}/inbox`);
  await expect(
    page.getByRole("region", { name: "Needs your action", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => performance.timeOrigin)).not.toBe(boot); // Only the explicit refresh navigated the document.
  expect(errors).toEqual([]);
});
