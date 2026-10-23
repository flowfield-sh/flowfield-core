import { dialogViews } from "./dialog-evidence";
import { resultRequest } from "./support";
import { projectWorkspace } from "./support";
import {
  fixtureCliProject,
  test,
  existingDirectory,
  state,
  checkout,
  cli,
  connectMcp,
  resultMessage,
  stubResourcePresence,
} from "./support";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect, type Route } from "@playwright/test";

test("managed review keeps its URL, binds the result, and preserves feedback on conflict", async ({
  page,
}, testInfo) => {
  await stubResourcePresence(page);
  fixtureCliProject([existingDirectory(join(state, "run-ui"))]);
  cli([
    "task",
    "create",
    "--workspace",
    await projectWorkspace(page.request, "run-ui"),
    "--id",
    "one",
    "--title",
    "Review a managed result",
    "--body",
    "Implement the behavior",
  ]);
  let run = {
    id: "attempt-one",
    project_id: "run-ui",
    task_id: "one",
    task_key: "T-1",
    revision: 7,
    status: "in_review",
    model: "fixture-model",
    effort: "low",
    created_at: "2026-01-01T10:00:00Z",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:01:00Z",
    base_commit: "a".repeat(40),
    result_commit: "b".repeat(40),
    result: {
      summary: "Implemented the loader",
      checks: "10 fixture checks passed",
      limitations: "",
    },
    feedback: "Preserve both inputs while resolving the previous conflict.",
    problem: null,
    code_available: false,
    usage: { total_tokens: 100, cached_input_tokens: 50, complete: true },
  };
  const version = () => ({
    files: [],
    id: run.id,
    project_id: run.project_id,
    task_id: run.task_id,
    task_key: run.task_key,
    version: 1,
    revision: run.revision,
    run_id: run.id,
    completion: "code",
    next_action: {
      owner: "human",
      action: "review_result",
      label: "Review changes",
    },
    source_commit: run.result_commit,
    candidate_commit: run.result_commit,
    report: run.result,
    status: run.status === "in_review" ? "ready" : run.status,
    created_at: run.created_at,
    target_branch: "integration",
    integration_id: "check-one",
    feedback: run.status === "changes_requested" ? run.feedback : "",
    problem: run.problem,
  });
  let previewAttempts = 0;
  let previewReady = false;
  let rejectOnce = true;
  let cancellations = 0;
  let moreFiles = true;
  let pendingFiles: Route | undefined;
  let cancelledFiles = false;
  page.on("requestfailed", (request) => {
    if (request.url().endsWith("/diff?offset=40")) cancelledFiles = true;
  });
  await page.route("**/api/workspaces/*/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/inspection") || path.includes("/inspections/")) {
      if (path.endsWith("/inspection") && !previewReady) {
        if (route.request().method() === "GET")
          return route.fulfill({ json: null });
        previewAttempts++;
        if (previewAttempts === 1)
          return route.fulfill({
            status: 409,
            json: {
              error: {
                code: "integration_busy",
                message: "Another integration owns this repository.",
              },
            },
          });
        previewReady = true;
      }
      return route.fulfill({
        json: {
          id: "preview-one",
          workspace_id: path.split("/")[3],
          project_id: "run-ui",
          result_id: run.id,
          target_branch: "main",
          commit: run.result_commit,
          created_at: "2026-10-19T00:00:00Z",
          status: "ready",
          command: "echo preview",
          workspace: "/fixture/worktree",
          locally_changed: false,
          source_changed: true,
          instructions_changed: false,
        },
      });
    }
    if (path.includes("/requests/result/"))
      return route.fulfill({
        json: resultRequest(version(), url.pathname.split("/")[3]),
      });
    if (path.endsWith("/thread"))
      return route.fulfill({
        json: { items: [resultMessage(version())], next_cursor: null },
      });
    if (path.includes("/thread/result"))
      return route.fulfill({ json: resultMessage(version()) });
    if (path.endsWith("/input-eligibility"))
      return route.fulfill({
        json: {
          enabled: true,
          reason: "idle",
          task_revision: 1,
          agreement_revision: 1,
          result_id: run.id,
          result_revision: run.revision,
          question_id: null,
          question_revision: null,
          run_id: null,
          pending_reply_id: null,
        },
      });
    if (path.endsWith("/replies")) {
      const body = route.request().postDataJSON();
      expect(body.binding.result_id).toBe(run.id);
      expect(body.action).toBe("changes");
      expect(body.binding.result_revision).toBe(7);
      if (rejectOnce) {
        rejectOnce = false;
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code: "stale_revision",
              message: "Review changed; inspect the latest result.",
            },
          },
        });
      }
      run = {
        ...run,
        revision: 8,
        status: "changes_requested",
        feedback: body.body,
      };
      return route.fulfill({ json: version() });
    }
    if (path.endsWith("/location"))
      return route.fulfill({
        json: {
          workspace: "/fixture/worktree",
          diff_command: "git diff BASE RESULT",
          try_command: "Run fixture checks",
        },
      });
    if (path.endsWith("/diff") && url.searchParams.has("offset")) {
      pendingFiles = route;
      return;
    }
    if (path.endsWith("/diff"))
      return route.fulfill({
        json: {
          files: Array.from({ length: 40 }, (_, id) => ({
            id,
            old_path: null,
            new_path: id === 0 ? "loader.py" : `file-${id}.py`,
            change: "added",
            old_mode: "000000",
            new_mode: "100644",
          })),
          total_files: moreFiles ? 41 : 40,
          next_offset: moreFiles ? 40 : null,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    const fileId = path.match(/\/diff\/(\d+)$/)?.[1];
    if (fileId !== undefined) {
      const id = Number(fileId);
      const filename = id === 0 ? "loader.py" : `file-${id}.py`;
      const lines = id === 1 ? 160 : 1;
      // Exercise the loading frame as well as the short and long rendered patches.
      await new Promise((resolve) => setTimeout(resolve, 150));
      return route.fulfill({
        json: {
          file: {
            id,
            old_path: null,
            new_path: filename,
            change: "added",
            old_mode: "000000",
            new_mode: "100644",
          },
          text: `diff --git a/${filename} b/${filename}\nnew file mode 100644\n--- /dev/null\n+++ b/${filename}\n@@ -0,0 +1,${lines} @@\n${Array.from({ length: lines }, () => "+print('implemented code')\n").join("")}`,
          binary: false,
          omitted_reason: null,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    }
    if (path.endsWith("/tasks/one/results"))
      return route.fulfill({
        json: {
          items: [version()],
          current_id: run.id,
          current_run_id: run.id,
          next_before: null,
        },
      });
    if (path.endsWith("/cancel")) {
      expect(route.request().postDataJSON().expected_revision).toBe(
        run.revision,
      );
      cancellations++;
      run = { ...run, status: "cancelled", revision: run.revision + 1 };
      return route.fulfill({ json: version() });
    }
    if (path.includes("/results/")) return route.fulfill({ json: version() });
    if (path.endsWith("/integrations/check-one"))
      return route.fulfill({ json: { id: "check-one", checks: [] } });
    if (path.includes("/runs/")) return route.fulfill({ json: run });
    return route.fallback();
  });
  for (const [status, action] of [["preparing", "Stop checks"]]) {
    run = { ...run, status };
    await page.goto(
      `/workspaces/${await projectWorkspace(page.request, "run-ui")}/results/attempt-one`,
    );
    const footer = page.getByRole("group", {
      name: "Task actions",
      exact: true,
    });
    await expect(
      footer.getByRole("button", { name: "Archive", exact: true }),
    ).toHaveCount(0);
    await footer.getByRole("button", { name: action, exact: true }).click();
    await expect(
      page.getByText("Code and output were kept.", { exact: false }),
    ).toBeVisible();
  }
  expect(cancellations).toBe(1);
  run = { ...run, status: "delivering" };
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "run-ui")}/results/attempt-one`,
  );
  await expect(
    page.getByText("Integrating changes…", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Cancel delivery" }),
  ).toHaveCount(0);
  run = { ...run, status: "in_review", revision: 7 };
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "run-ui")}/results/attempt-one`,
  );
  await page.reload();
  const runs = page.getByRole("region", {
    name: "Proposed result",
    exact: true,
  });
  await expect(runs).toContainText("Implemented the loader");
  await expect(runs).toContainText("Project: run-ui · Branch: integration");
  await expect(
    runs.getByRole("heading", { name: "Delivery", exact: true }),
  ).toHaveCount(0);
  await expect(
    runs.getByText("Requested in the previous review", { exact: true }),
  ).toHaveCount(0);
  await expect.poll(() => previewAttempts).toBe(2);
  await expect(
    runs.getByRole("button", { name: "Prepare another copy" }),
  ).toHaveCount(0);
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeHidden();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.screenshot({
        path: testInfo.outputPath(`result-summary-${colorScheme}-${width}.png`),
      });
    }
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await runs.getByText("Code changes", { exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "implemented code",
  );
  const browser = runs.locator(".diff-browser");
  const preview = browser.locator(".file-preview");
  const files = browser.getByRole("navigation", { name: "Changed files" });
  await files.getByRole("button", { name: "More files" }).click();
  await expect.poll(() => !!pendingFiles).toBe(true);
  moreFiles = false;
  await page.evaluate(() =>
    window.dispatchEvent(new Event("flowfield:service-recovered")),
  );
  try {
    await expect.poll(() => cancelledFiles).toBe(true);
  } finally {
    await pendingFiles!.fulfill({
      json: {
        files: [
          { id: 40, new_path: "stale.py", old_path: null, change: "added" },
        ],
        total_files: 41,
        next_offset: null,
        base_commit: run.base_commit,
        result_commit: run.result_commit,
      },
    });
  }
  await expect(files.getByRole("button", { name: /stale.py/ })).toHaveCount(0);
  await expect(files.getByRole("button", { name: "More files" })).toHaveCount(
    0,
  );
  await expect(files.getByRole("button")).toHaveCount(40);
  await files.evaluate((element) => (element.scrollTop = 0));
  const pane = page.locator(".entity-pane-body");
  const geometry = () =>
    browser.evaluate((el) => el.getBoundingClientRect().height);
  const height = await geometry();
  const clickFile = async (name: string) => {
    const button = files.getByRole("button", { name, exact: true });
    expect(
      await button.evaluate((el) => {
        const box = el.getBoundingClientRect();
        return el.contains(
          document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2,
          ),
        );
      }),
    ).toBe(true);
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    // Use a real pointer without Playwright scrolling the whole button into view.
    await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  };
  expect(await files.evaluate((el) => el.clientHeight)).toBe(
    await preview.evaluate((el) => el.clientHeight),
  );
  expect(await files.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(
    true,
  );
  for (const offset of [100, 160]) {
    await browser.evaluate((el, offset) => {
      const pane = el.closest(".entity-pane-body")!;
      pane.scrollTop +=
        el.getBoundingClientRect().top -
        pane.getBoundingClientRect().top -
        offset;
    }, offset);
    // Let the feed capture the deliberate reading movement before measuring it.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    const top = await pane.evaluate((el) => el.scrollTop);
    await clickFile("Added file-1.py");
    await expect(preview).toContainText("Loading file…");
    expect(await geometry()).toBe(height);
    await expect(preview.locator(".diff-code-insert")).toHaveCount(160);
    await expect
      .poll(() => preview.evaluate((el) => el.scrollHeight > el.clientHeight))
      .toBe(true);
    expect(await geometry()).toBe(height);
    expect(await pane.evaluate((el) => el.scrollTop)).toBe(top);
    await preview.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await clickFile("Added file-2.py");
    await expect(preview.locator(".diff-code-insert")).toHaveCount(1);
    expect(await preview.evaluate((el) => el.scrollTop)).toBe(0);
    expect(await geometry()).toBe(height);
    expect(await pane.evaluate((el) => el.scrollTop)).toBe(top);
  }
  const capturePreview = async (size: string) => {
    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await expect
        .poll(() =>
          page.locator("html").evaluate((el) => el.classList.contains("dark")),
        )
        .toBe(theme === "dark");
      await page.screenshot({
        path: testInfo.outputPath(`code-preview-${size}-${theme}.png`),
        animations: "disabled",
      });
    }
    await page.emulateMedia({ colorScheme: "light" });
  };
  await capturePreview("desktop");
  await files.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await files
    .getByRole("button", { name: "Added file-39.py", exact: true })
    .scrollIntoViewIfNeeded();
  await expect(
    files.getByRole("button", { name: "Added file-39.py", exact: true }),
  ).toBeInViewport();
  await page.setViewportSize({ width: 390, height: 640 });
  await files.evaluate((el) => {
    el.scrollTop = 0;
  });
  const mobileHeight = await geometry();
  await files
    .getByRole("button", { name: "Added file-1.py", exact: true })
    .click();
  await expect(preview.locator(".diff-code-insert")).toHaveCount(160);
  expect(await geometry()).toBe(mobileHeight);
  expect(
    await preview.evaluate(
      (el) => el.clientHeight > 0 && el.scrollHeight > el.clientHeight,
    ),
  ).toBe(true);
  await runs.getByRole("button", { name: "Split", exact: true }).click();
  expect(await geometry()).toBe(mobileHeight);
  await preview.scrollIntoViewIfNeeded();
  await capturePreview("mobile");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Feedback for this result", exact: true })
    .fill("Include the source path in errors.");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(
    page.locator("[data-sonner-toast][data-type=error]"),
  ).toContainText("Review changed");
  await expect(
    page.getByRole("textbox", {
      name: "Feedback for this result",
      exact: true,
    }),
  ).toHaveValue("Include the source path in errors.");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(
    runs.getByRole("heading", { name: "Review request", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", {
      name: "Feedback for this result",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(/results\/attempt-one$/);
});

test("validated result approval delivers real Git code with the worker queue paused", async ({
  page,
  request,
}, testInfo) => {
  // Several real scheduler/delivery transitions each have bounded waits below.
  test.setTimeout(60_000);
  const seed = JSON.parse(
    execFileSync(
      join(checkout, ".venv/bin/python"),
      [
        join(checkout, "scripts/create-integration-demo.py"),
        join(state, "delivery-fixture"),
        "--state",
        state,
      ],
      { encoding: "utf8" },
    ),
  );
  const base = `/api/workspaces/${await projectWorkspace(page.request, seed.project)}`;
  const mcp = await connectMcp(request);
  const read = await mcp("get_result", {
    workspace_id: await projectWorkspace(request, seed.project),
    result_id: seed.result,
  });
  expect(read.status).toBe("ready");
  expect(
    cli([
      "task",
      "results",
      "show",
      seed.result,
      "--workspace",
      await projectWorkspace(request, seed.project),
    ]).candidate_commit,
  ).toBe(seed.candidate);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, seed.project)}/inbox`,
  );
  await page.locator(".inbox-history-row").filter({ hasText: "T-1" }).click();
  await page
    .locator(".entity-pane-title")
    .getByRole("link", { name: /T-1:/ })
    .click();
  await expect(page).toHaveURL(
    `/workspaces/${await projectWorkspace(page.request, seed.project)}/tasks/T-1`,
  );
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, seed.project)}/results/${seed.result}`,
  );
  const result = page.getByRole("region", { name: "Proposed result" });
  await expect(result).toContainText("project checks passed");
  await expect(result).toContainText("Passed");
  await expect(result).toContainText(
    "Project: Harbor · Integration trial · Branch: integration",
  );
  await expect(result.locator(".diff-code-insert")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Ask worker", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.getByLabel("Testing notes or approval comment (optional)", {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByLabel("Testing notes or approval comment (optional)", { exact: true })
    .fill("Keyboard checked; mobile not tested.");
  await page.screenshot({ path: testInfo.outputPath("approval-comment.png") });
  const approvalResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`${base}/results/${seed.result}/review`) &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  expect((await approvalResponse).ok()).toBe(true);
  await page.goto(`${base.replace("/api", "")}/tasks/T-1`);
  const approval = page.locator('[data-kind="approval"] .activity-entry');
  await expect(approval).toBeVisible();
  if (
    !(await approval.evaluate(
      (element) => (element as HTMLDetailsElement).open,
    ))
  )
    await approval.locator(":scope > summary").click();
  await expect(
    page.getByText("Keyboard checked; mobile not tested.", { exact: true }),
  ).toBeVisible();
  await page.goto(`${base.replace("/api", "")}/results/${seed.result}`);
  // The shared scheduler also handles other journeys before attempting delivery.
  await expect(
    page.getByRole("button", { name: "Retry integration", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  expect((await (await request.get(`${base}/tasks/T-1`)).json()).status).toBe(
    "in_review",
  );
  const preserved = readFileSync(join(seed.repository, "README.md"), "utf8");
  expect(preserved).toContain("Unsaved human planning notes");
  writeFileSync(join(state, "preserved-human-readme.txt"), preserved);
  execFileSync("git", [
    "-C",
    seed.repository,
    "restore",
    "--worktree",
    "README.md",
  ]);
  await page
    .getByRole("button", { name: "Retry integration", exact: true })
    .click();
  // Delivery is asynchronous: the service scheduler also handles other journeys.
  await expect
    .poll(
      async () =>
        (await (await request.get(`${base}/tasks/T-1`)).json()).status,
      { timeout: 15000 },
    )
    .toBe("done");
  await expect(result).not.toContainText("Delivered to integration.");
  const task = await (await request.get(`${base}/tasks/T-1`)).json();
  expect(task.status).toBe("done");
  const replay = await mcp("review_result", {
    workspace_id: await projectWorkspace(request, seed.project),
    result_id: seed.result,
    review: {
      expected_revision: seed.result_revision,
      candidate_commit: seed.candidate,
      action: "approve",
      author: "fixture",
    },
  });
  expect(replay.status).toBe("delivered");
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", seed.repository, ...args], {
      encoding: "utf8",
    }).trim();
  expect(git("rev-parse", "integration")).toBe(seed.candidate);
  expect(git("rev-parse", "HEAD")).toBe(seed.candidate);
  expect(existsSync(join(seed.repository, "catalog.py"))).toBe(true);
  expect(git("status", "--porcelain")).toBe("");
  await page.reload();
  await expect(result).not.toContainText("Task complete.");
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  await result.getByText(/^Flowfield: .*project checks passed$/).click();
  await expect(
    result.getByText("Project commands", { exact: true }),
  ).toBeVisible();
  await page.goto(`${base.replace("/api", "")}/tasks/T-1`);
  const timeline = page.locator(".conversation-messages");
  const history = timeline.locator('[data-kind="attempt"]');
  await expect(history).toHaveCount(1);
  await history.locator(".activity-entry > summary").click();
  await expect(history.locator(".run-activity")).toBeVisible();
  await expect(result.locator(".run-activity")).toHaveCount(0);
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Keep this question draft while inspecting evidence.");
  await expect(
    history.getByText("Execution details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("Message coordinator", { exact: true }),
  ).toHaveValue("Keep this question draft while inspecting evidence.");
  await page.getByLabel("Message coordinator", { exact: true }).fill("");
  await expect(
    history.getByRole("heading", { name: "Worker report", exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  // Saved exact-execution URLs still reveal only the requested historical evidence.
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, seed.project)}/tasks/T-1/history/delivery:${seed.result}`,
  );
  await expect(
    history.getByRole("heading", { name: "Review 1 delivery", exact: true }),
  ).toBeVisible();
  await expect(history).toContainText("Approved by You");
  const advanced = git(
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@example.invalid",
    "commit-tree",
    `${seed.candidate}^{tree}`,
    "-p",
    seed.candidate,
    "-m",
    "External target advance",
  );
  // No unrelated SSE refresh may hide a stale retained result on navigation.
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  git("update-ref", "refs/heads/integration", advanced);
  // Availability is observed by the service scheduler. Wait for that observation
  // before testing navigation refresh, while SSE remains disconnected.
  await expect
    .poll(
      async () => {
        const observed = await (
          await request.get(`${base}/results/${seed.result}`)
        ).json();
        return observed.next_action?.action;
      },
      { timeout: 15000 },
    )
    .toBe("revalidate");
  await history
    .getByRole("link", { name: "Review these changes" })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Check destination" }),
  ).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.getByRole("button", { name: "Check destination" }).click();
  await expect(
    result.getByRole("region", { name: "Current branch checks" }),
  ).toContainText("Passed");
  await expect(
    page.getByRole("button", { name: "Check destination" }),
  ).toHaveCount(0);
  expect(git("rev-parse", "integration")).toBe(advanced);
  await expect(result).not.toContainText("Task complete.");
  await result
    .getByRole("region", { name: "Current branch checks" })
    .locator(":scope > summary")
    .click();
  await result.getByRole("link", { name: "Open branch check details" }).click();
  await expect(
    history.getByRole("heading", { name: "Review 1 branch checks" }),
  ).toBeVisible();
});

test("adoption preserves repository guidance and standalone installation stays explicit", async ({
  page,
  request,
}) => {
  const root = existingDirectory(join(state, "adoption-guidance"));
  execFileSync("git", ["init", "-b", "main", root], { stdio: "ignore" });
  const agents = join(root, "AGENTS.md");
  const original = "# Repository rules\n\nKeep our conventions.\n";
  writeFileSync(agents, original);
  writeFileSync(join(root, "AGENTS.override.md"), "Local override\n");
  const adopted = fixtureCliProject([root]);
  await page.goto("/projects/" + adopted.id);
  await expect(
    page.getByRole("article", { name: "Coordinator welcome" }),
  ).toContainText("workspace guidance");
  expect(readFileSync(agents, "utf8")).toBe(original);
  expect(existsSync(join(root, ".agents"))).toBe(false);
  const before = await (
    await request.get("/api/projects/" + adopted.id + "/workspace")
  ).json();
  cli(["project", "guidance", "install"], root);
  expect(readFileSync(agents, "utf8").startsWith(original)).toBe(true);
  expect(
    existsSync(join(root, ".agents/skills/flowfield-coordinator/SKILL.md")),
  ).toBe(true);
  writeFileSync(agents, readFileSync(agents, "utf8") + "\nNew local rule\n");
  cli(["project", "guidance", "remove"], root);
  expect(readFileSync(agents, "utf8")).toBe(original + "\nNew local rule\n");
  expect(readFileSync(join(root, "AGENTS.override.md"), "utf8")).toBe(
    "Local override\n",
  );
  expect(
    existsSync(join(root, ".agents/skills/flowfield-coordinator/SKILL.md")),
  ).toBe(false);
  await page.reload();
  await expect(
    page.getByRole("tablist", { name: "Open resources" }),
  ).toBeVisible();
  expect(
    await (
      await request.get("/api/projects/" + adopted.id + "/workspace")
    ).json(),
  ).toEqual(before);
});

test("managed answers show durable pause, safe edits and immutable correction input", async ({
  page,
  request,
}) => {
  const fixture = (operation: string) =>
    JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "--project",
          checkout,
          "python",
          join(checkout, "tests/input_browser_fixture.py"),
          state,
          operation,
        ],
        { encoding: "utf8" },
      ),
    );
  fixture("create");
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "input-browser")}/requests/question/which-records`,
  );
  const inputArea = page.getByRole("form", { name: "Task input", exact: true });
  await expect(
    page.getByRole("region", { name: "Request", exact: true }),
  ).toContainText("Which records?");
  await expect(
    page.getByRole("button", { name: "Stop task", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Reply", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Your answer", { exact: true }).fill("All records");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Request", exact: true }),
  ).toContainText("All records");
  await expect(
    page.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveCount(0);
  // The question loads after eligibility. An early click must wait for its answer.
  await page.route("**/questions/which-records", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.continue();
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(request, "input-browser")}/requests/question/which-records`,
  );
  const detail = page.getByRole("region", {
    name: "Request",
    exact: true,
  });
  await expect(inputArea).toContainText(
    "Enable the queue when ready to continue.",
  );
  await expect(
    detail.getByRole("button", { name: "Retract", exact: true }),
  ).toHaveCount(0);
  await detail
    .getByRole("button", { name: "Edit answer", exact: true })
    .click();
  const editedAnswer = detail.getByLabel("Your answer", { exact: true });
  await expect(editedAnswer).toBeFocused();
  await editedAnswer.fill("");
  await expect(editedAnswer).toBeVisible();
  await expect(
    detail.getByRole("button", { name: "Send answer", exact: true }),
  ).toBeDisabled();
  await detail
    .getByRole("button", { name: "Cancel edit", exact: true })
    .click();
  await expect(editedAnswer).toHaveCount(0);
  await detail
    .getByRole("button", { name: "Edit answer", exact: true })
    .click();
  await expect(editedAnswer).toHaveValue("All records");
  await editedAnswer.fill("");
  await editedAnswer.fill("Only favourites");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail).toContainText("Only favourites");
  await page.reload();
  await expect(
    detail.getByText("Only favourites", { exact: true }),
  ).toBeVisible();
  await expect(inputArea).toContainText(
    "Enable the queue when ready to continue.",
  );
  const counts = await request.get(
    `/api/workspaces/${await projectWorkspace(page.request, "input-browser")}/view/attention?column=action`,
  );
  expect((await counts.json()).total).toBe(0);
  fixture("consume");
  await page.reload();
  await expect(
    detail.getByRole("button", { name: "Stop task", exact: true }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("button", { name: "Edit answer", exact: true }),
  ).toHaveCount(0);
  // A running worker closes input. External correction is immutable evidence,
  // and must appear in the same task feed rather than a second question editor.
  await expect(detail.getByRole("textbox")).toHaveCount(0);
  const original = await (
    await request.get(
      `/api/workspaces/${await projectWorkspace(page.request, "input-browser")}/questions/which-records`,
    )
  ).json();
  const corrected = await request.post(
    `/api/workspaces/${await projectWorkspace(page.request, "input-browser")}/questions/which-records/correction`,
    {
      data: {
        expected_revision: original.revision,
        answer: "I changed my mind: all records.",
      },
    },
  );
  expect(corrected.ok()).toBe(true);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "input-browser")}/tasks/T-1`,
  );
  await expect(page.locator('[data-kind="answer"]')).toContainText([
    "All records",
    "Only favourites",
    "I changed my mind: all records.",
  ]);
});

test("inspection preserves exact versions, preview edits and direct approval", async ({
  page,
  request,
}, testInfo) => {
  // Two result generations and multiple Git copies need a bounded hosted-runner budget.
  test.setTimeout(60000);
  const project = "inspection-browser";
  let preparations = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.endsWith("/inspection")
    )
      preparations++;
  });
  const fixture = (operation: string) =>
    execFileSync(
      "uv",
      [
        "run",
        "--project",
        checkout,
        "python",
        join(checkout, "tests/inspection_browser_fixture.py"),
        state,
        operation,
      ],
      { encoding: "utf8" },
    );
  fixture("create");
  const path = `/api/workspaces/${await projectWorkspace(page.request, project)}`;
  let first: { id: string; revision: number; candidate_commit: string };
  await expect(async () => {
    const versions = await (
      await request.get(`${path}/tasks/try/results`)
    ).json();
    expect(versions.items[0]?.status).toBe("ready");
    first = versions.items[0];
  }).toPass();
  await page.route(`**/api/workspaces/*/results/*`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.continue();
  });

  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/tasks/T-1`,
  );
  await expect(page.getByLabel("Current task definition")).toBeVisible();
  await expect(page.locator(".entity-pane-footer")).toHaveCount(0);
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/results/${first!.id}`,
  );
  let selectedPreview = first!.id;
  const preview = () =>
    page
      .locator(`.task-result[data-result-id="${selectedPreview}"]`)
      .getByRole("region", { name: "Run command", exact: true });
  await expect(preview()).toBeVisible();
  await expect(preview().locator("summary")).toHaveCount(0);
  await expect(
    preview().getByRole("textbox", { name: "Terminal commands" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Inspection", exact: true }),
  ).toHaveCount(0);
  await expect(
    preview().getByText("Inspection details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    preview().getByRole("button", { name: "Copy commands", exact: true }),
  ).toBeVisible();
  const copy = await (
    await request.get(`${path}/inspection?result_id=${first!.id}`)
  ).json();
  expect(copy.commit).toBe(first!.candidate_commit);
  expect(copy.command).toMatch(/^\/bin\/sh /);
  await expect(
    preview().getByRole("textbox", { name: "Terminal commands" }),
  ).toHaveValue(copy.command);
  expect(preparations).toBe(1);
  await page.reload();
  await expect(
    preview().getByRole("textbox", { name: "Terminal commands" }),
  ).toHaveValue(copy.command);
  expect(preparations).toBe(1); // Reopening reads the existing copy without another write.
  await page.setViewportSize({ width: 390, height: 844 });
  const copyButton = preview().getByRole("button", {
    name: "Copy commands",
    exact: true,
  });
  await expect
    .poll(async () => {
      const box = (await copyButton.boundingBox())!;
      return box.x >= 0 && box.x + box.width <= 390;
    })
    .toBe(true);
  await expect
    .poll(
      async () =>
        (await preview()
          .getByRole("textbox", { name: "Terminal commands" })
          .boundingBox())!.height,
    )
    .toBeLessThanOrEqual(128);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await preview()
    .getByRole("button", { name: "Copy commands", exact: true })
    .click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    copy.command,
  );
  expect(
    execFileSync("/bin/sh", ["-c", copy.command], { encoding: "utf8" }),
  ).toContain("First greeting");
  writeFileSync(join(copy.workspace, "app.py"), "print('Preview edit')\n");
  await expect(
    page.getByRole("button", { name: "Record testing", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  const approvalComment = page.getByLabel(
    "Testing notes or approval comment (optional)",
    { exact: true },
  );
  await expect(approvalComment).toBeFocused();
  await approvalComment.fill("Partial test, not ready to approve yet.");
  expect(
    (await (await request.get(`${path}/results/${first!.id}`)).json())
      .approved_at,
  ).toBeNull();
  await page
    .getByRole("button", { name: "Cancel approval", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Result", exact: true }),
  ).toBeVisible();
  await expect(approvalComment).toHaveCount(0);
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  const feedback = page.getByLabel("Feedback for this result", { exact: true });
  await expect(feedback).toBeFocused();
  await feedback.fill("A bound result change request");
  await feedback.fill("");
  await expect(feedback).toBeVisible();
  await page
    .getByRole("button", { name: "Cancel feedback", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Result", exact: true }),
  ).toBeVisible();
  await expect(feedback).toHaveCount(0);
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await feedback.fill("Make the greeting useful.");
  await page
    .getByRole("button", { name: "Send feedback", exact: true })
    .click();
  await expect(
    page.getByText("Changes requested. The worker queue is paused", {
      exact: false,
    }),
  ).toBeVisible();
  fixture("successor");
  await expect(async () => {
    const versions = await (
      await request.get(`${path}/tasks/try/results`)
    ).json();
    expect(versions.items[0]?.status).toBe("ready");
    expect(versions.items[0]?.id).not.toBe(first!.id);
  }).toPass();
  selectedPreview = (
    await (await request.get(`${path}/tasks/try/results`)).json()
  ).items[0].id;
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/results/${selectedPreview}`,
  );
  await expect(preview()).toBeVisible();
  await expect(preview().locator("summary")).toHaveCount(0);
  await expect(
    preview().getByRole("textbox", { name: "Terminal commands" }),
  ).toBeVisible();
  await expect(preview().getByText(/T-1 · Result 2/)).toHaveCount(0);
  const second = (await (await request.get(`${path}/tasks/try/results`)).json())
    .items[0];
  const next = await (
    await request.get(`${path}/inspection?result_id=${second.id}`)
  ).json();
  expect(next.workspace).not.toBe(copy.workspace);
  expect(
    execFileSync("/bin/sh", ["-c", next.command], { encoding: "utf8" }),
  ).toContain("Useful successor");
  expect(readFileSync(join(copy.workspace, "app.py"), "utf8")).toContain(
    "Preview edit",
  );
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
  // Wait for the exact candidate's real Git delivery before testing presentation.
  // The full suite's shared scheduler can take longer than a UI assertion budget.
  await expect(async () => {
    const versions = await (
      await request.get(`${path}/tasks/try/results`)
    ).json();
    expect(
      versions.items.find((item: { id: string }) => item.id === second.id),
    ).toMatchObject({
      status: "delivered",
      candidate_commit: second.candidate_commit,
    });
  }).toPass({ timeout: 30000 });
  await expect(
    page.getByText("Delivered to delivery.", { exact: false }),
  ).toHaveCount(0);
  await page.goto(`/projects/${project}`);
  await expect(
    page.getByRole("button", { name: "Try project", exact: true }),
  ).toHaveCount(0);
  selectedPreview = first!.id;
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/results/${first!.id}`,
  );
  await expect(preview().locator("summary")).toHaveCount(0);
  await expect(
    preview().getByText("excluding edits in this copy.", { exact: false }),
  ).toBeVisible();
  await expect(
    preview().getByText("This preview belongs to this earlier result.", {
      exact: false,
    }),
  ).toBeVisible();
  await preview().getByRole("button", { name: "Prepare another copy" }).click();
  await expect(
    preview().getByText("excluding edits in this copy.", { exact: false }),
  ).toHaveCount(0);
  expect(readFileSync(join(copy.workspace, "app.py"), "utf8")).toContain(
    "Preview edit",
  );
  await page.goto(`/projects/${project}/edit/integration`);
  const runCommand = page.getByLabel("Run command", { exact: true });
  await expect(runCommand).toHaveValue("python app.py");
  await runCommand.fill("python app.py --draft");
  const runSettings = await (
    await request.get(`/api/projects/${project}/inspection/settings`)
  ).json();
  await request.put(`/api/projects/${project}/inspection/settings`, {
    data: {
      expected_revision: runSettings.revision,
      run_command: "python app.py --elsewhere",
    },
  });
  await page
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  await expect(runCommand).toHaveValue("python app.py --draft");
  await expect(
    page.getByRole("button", { name: "Load current run command" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Load current run command" }).click();
  const runDialog = page.getByRole("alertdialog");
  await expect(
    runDialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await dialogViews(page, testInfo, "integration-run-reload");
  await page.keyboard.press("Escape");
  await expect(runCommand).toHaveValue("python app.py --draft");
  await page.getByRole("button", { name: "Load current run command" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load current run command", exact: true })
    .click();
  await expect(runCommand).toHaveValue("python app.py --elsewhere");
  await runCommand.fill("python app.py");
  const runSaved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/projects/${project}/inspection/settings`) &&
      response.request().method() === "PUT",
  );
  await page
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  expect((await runSaved).ok()).toBe(true);
  await expect(
    page.getByRole("button", {
      name: "Save integration settings",
      exact: true,
    }),
  ).toBeDisabled();
  const cleanSettings = await (
    await request.get(`/api/projects/${project}/inspection/settings`)
  ).json();
  const externalUpdate = await request.put(
    `/api/projects/${project}/inspection/settings`,
    {
      data: {
        expected_revision: cleanSettings.revision,
        run_command: "python app.py --updated",
      },
    },
  );
  expect(externalUpdate.ok()).toBe(true);
  await expect(runCommand).toHaveValue("python app.py --updated");
});

test("outcomes finish reports, preserve partial work and offer one contextual remedy", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  execFileSync("uv", [
    "run",
    "--project",
    checkout,
    "python",
    join(checkout, "tests/outcome_browser_fixture.py"),
    state,
  ]);
  const result = async (kind: string) =>
    (
      await (
        await request.get(
          `/api/workspaces/${await projectWorkspace(page.request, "outcome-" + kind)}/tasks/work/results`,
        )
      ).json()
    ).items[0];
  await expect(async () =>
    expect((await result("report"))?.status).toBe("delivered"),
  ).toPass();
  // Stagger independently loaded records, including content taller than the viewport.
  let resultDelay = 0;
  let pendingReads = 0;
  await page.route(`**/api/workspaces/*/results/*`, async (route) => {
    pendingReads++;
    try {
      const response = await route.fetch().catch(() => null);
      if (!response) return;
      const body = await response.json();
      body.report.summary += "\n\n" + "A detailed finding.\n\n".repeat(35);
      await new Promise((resolve) => setTimeout(resolve, resultDelay));
      await route.fulfill({ response, json: body });
    } finally {
      pendingReads--;
    }
  });
  await page.route(
    (url) =>
      decodeURIComponent(url.pathname).includes(`/tasks/work/thread/result:`),
    async (route) => {
      pendingReads++;
      try {
        await new Promise((resolve) =>
          setTimeout(resolve, resultDelay ? 0 : 500),
        );
        await route.continue();
      } finally {
        pendingReads--;
      }
    },
  );
  const reportPane = page.locator(".entity-pane-body");
  for (const delay of [0, 250]) {
    resultDelay = delay;
    await page.goto(
      `/workspaces/${await projectWorkspace(request, "outcome-report")}/inbox?inbox=delivered`,
    );
    // Completed reports live in Delivered, with exact versioned result links.
    await page
      .getByRole("tabpanel", { name: "Delivered", exact: true })
      .getByRole("link", { name: /Report outcome/ })
      .click();
    await expect(
      page.getByRole("region", { name: "Proposed result", exact: true }),
    ).toContainText("A detailed finding.");
    // Standalone Requests open at the ask, without jumping to task activity.
    await expect.poll(() => reportPane.evaluate((el) => el.scrollTop)).toBe(0);
    await expect(
      page.getByRole("button", { name: "Back", exact: true }),
    ).toBeDisabled();
    await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
  }
  await page.goto(
    `/workspaces/${await projectWorkspace(request, "outcome-report")}/tasks/T-1`,
  );
  await expect(page.locator(".entity-pane-footer")).toHaveCount(0);
  await page
    .locator('[data-kind="result"] details.activity-entry > summary')
    .click();
  await expect(
    page.getByRole("link", { name: "Access results", exact: true }),
  ).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(request, "outcome-report")}/results/${(await result("report")).id}`,
  );
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "outcome-report")}/results/${(await result("report")).id}`,
  );
  await expect(
    page.getByText("Findings delivered.", { exact: false }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  expect((await result("report")).approved_at).toBeNull();
  await expect(async () =>
    expect((await result("partial"))?.problem_code).toBe("partial_outcome"),
  ).toPass();
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "outcome-partial")}/results/${(await result("partial")).id}`,
  );
  await expect(
    page.getByRole("heading", { name: "Work remaining" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Request follow-up", exact: true })
    .click();
  await page
    .getByLabel("Feedback for this result", { exact: true })
    .fill("Finish the second behavior");
  await page
    .getByRole("button", { name: "Send feedback", exact: true })
    .click();
  await expect(
    page.getByText("Changes requested. The worker queue is paused", {
      exact: false,
    }),
  ).toBeVisible();
  expect((await result("partial")).feedback).toBe("Finish the second behavior");
  await expect(async () =>
    expect((await result("checks"))?.problem_code).toBe("checks_failed"),
  ).toPass();
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "outcome-checks")}/results/${(await result("checks")).id}`,
  );
  const recovery = page.getByRole("region", { name: "Result recovery" });
  const actions = page.getByRole("group", {
    name: "Task actions",
    exact: true,
  });
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toBeVisible();
  await expect(recovery).toBeVisible();
  // The result opens its preview automatically. Let that repository operation
  // finish before this journey requests a correction against the same checkout.
  await expect(
    page.getByRole("region", { name: "Run command", exact: true }),
  ).toContainText("Set the project run command");
  await expect(
    actions.getByRole("button", { name: "Archive", exact: true }),
  ).toHaveCount(0);
  await expect(recovery.getByRole("link")).toHaveCount(0);
  const taskPath = `/api/workspaces/${await projectWorkspace(page.request, "outcome-checks")}/tasks/work`;
  const originalTask = await (await request.get(taskPath)).json();
  expect(
    (
      await request.put(taskPath, {
        data: {
          expected_revision: originalTask.revision,
          body: "Deliver the revised agreed outcome",
        },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toHaveCount(0);
  const revisedTask = await (await request.get(taskPath)).json();
  expect(
    (
      await request.post(taskPath + "/reconcile", {
        data: {
          expected_revision: revisedTask.revision,
          completion: "code",
          note: "Human clarified the remaining outcome",
        },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toBeVisible();
  await actions.getByRole("button", { name: "Request correction" }).click();
  await expect(
    page.getByText("Changes requested. The worker queue is paused", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(async () =>
    expect((await result("setup"))?.problem_code).toBe("runtime_setup_failed"),
  ).toPass();
  let releaseRun!: () => void;
  const runReady = new Promise<void>((resolve) => {
    releaseRun = resolve;
  });
  const setupWorkspace = await projectWorkspace(page.request, "outcome-setup");
  await page.route(
    `**/api/workspaces/${setupWorkspace}/runs/*`,
    async (route) => {
      await runReady;
      await route.continue();
    },
  );
  await page.goto(
    `/workspaces/${setupWorkspace}/results/${(await result("setup")).id}`,
  );
  await expect(
    actions.getByRole("button", { name: "Fix project setup" }),
  ).toBeDisabled();
  releaseRun();
  await actions.getByRole("link", { name: "Fix project setup" }).click();
  await expect(page).toHaveURL(/edit\/integration/);
  // Stop polling and drain handlers before removing interception. Removing routes
  // while a delayed response is pending can continue it before fulfill runs.
  await page.goto("about:blank");
  await expect.poll(() => pendingReads).toBe(0);
  await page.unrouteAll({ behavior: "wait" });
});
