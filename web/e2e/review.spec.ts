import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import {
  test,
  existingDirectory,
  state,
  checkout,
  cli,
  closeOverlay,
  connectMcp,
  resultMessage,
} from "./support";

test("managed review keeps its URL, binds the result, and preserves feedback on conflict", async ({
  page,
}, testInfo) => {
  cli([
    "project",
    "init",
    existingDirectory(join(state, "run-ui")),
    "--prefix",
    "RUN",
  ]);
  cli([
    "task",
    "create",
    "--project",
    "run-ui",
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
    task_key: "RUN-1",
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
    feedback: "",
    problem: null,
    code_available: false,
    usage: { total_tokens: 100, cached_input_tokens: 50, complete: true },
  };
  const version = () => ({
    id: run.id,
    project_id: run.project_id,
    task_id: run.task_id,
    task_key: run.task_key,
    version: 1,
    revision: run.revision,
    run_id: run.id,
    completion: "code",
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
  let rejectOnce = true;
  let cancellations = 0;
  await page.route("**/api/projects/run-ui/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
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
          total_files: 40,
          next_offset: null,
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
  for (const [status, action] of [
    ["preparing", "Stop checks"],
    ["delivering", "Cancel delivery"],
  ]) {
    run = { ...run, status };
    await page.goto("/projects/run-ui/tasks/RUN-1/result/attempt-one");
    const footer = page.getByRole("group", {
      name: "Task actions",
      exact: true,
    });
    await expect(
      footer.getByRole("button", { name: "Archive", exact: true }),
    ).toBeVisible();
    await footer.getByRole("button", { name: action, exact: true }).click();
    await expect(
      page.getByText("Code and check output were kept.", { exact: false }),
    ).toBeVisible();
  }
  expect(cancellations).toBe(2);
  run = { ...run, status: "in_review", revision: 7 };
  await page.goto("/projects/run-ui/tasks/RUN-1/result/attempt-one");
  await page.reload();
  const runs = page.getByRole("region", {
    name: "Proposed result",
    exact: true,
  });
  await expect(runs).toContainText("Implemented the loader");
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeHidden();
  await runs.getByText("Code changes", { exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "implemented code",
  );
  const browser = runs.locator(".diff-browser");
  const preview = browser.locator(".file-preview");
  const files = browser.getByRole("navigation", { name: "Changed files" });
  const pane = page.locator(".entity-overlay-body");
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
      const pane = el.closest(".entity-overlay-body")!;
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
  await page.screenshot({
    path: testInfo.outputPath("code-preview-desktop.png"),
  });
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
  await page.screenshot({
    path: testInfo.outputPath("code-preview-mobile.png"),
  });
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
  await expect(page.getByRole("alert")).toContainText("Review changed");
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
  await expect(page).toHaveURL(/tasks\/RUN-1\/result\/attempt-one$/);
});

test("validated result approval delivers real Git code with the worker queue paused", async ({
  page,
  request,
}, testInfo) => {
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
  const base = `/api/projects/${seed.project}`;
  const mcp = await connectMcp(request);
  const read = await mcp("get_result", {
    project_id: seed.project,
    result_id: seed.result,
  });
  expect(read.status).toBe("ready");
  expect(
    cli(["task", "results", "show", seed.result, "--project", seed.project])
      .candidate_commit,
  ).toBe(seed.candidate);
  await page.goto(`/projects/${seed.project}/inbox`);
  await page.getByRole("link", { name: /INT-1.*Review changes/ }).click();
  await expect(page).toHaveURL(
    new RegExp(`/conversation/result:${seed.result}$`),
  );
  const result = page.getByRole("region", { name: "Proposed result" });
  await expect(result).toContainText("project checks passed");
  await expect(result).toContainText("Passed");
  await expect(result).toContainText("Destination: integration");
  await expect(result.locator(".diff-code-insert")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Ask worker", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("group", { name: "Message task context" }),
  ).toContainText("selected result");
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
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.getByText("Keyboard checked; mobile not tested.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Retry integration", exact: true }),
  ).toBeVisible();
  expect((await (await request.get(`${base}/tasks/INT-1`)).json()).status).toBe(
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
  await expect(result).toContainText("Delivered to integration.");
  const task = await (await request.get(`${base}/tasks/INT-1`)).json();
  expect(task.status).toBe("done");
  const replay = await mcp("review_result", {
    project_id: seed.project,
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
  await expect(result).toContainText("Task complete.");
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  const timeline = page.locator(".conversation-messages");
  const history = timeline.locator('[data-kind="attempt"]');
  await expect(history).toHaveCount(1);
  await expect(history.locator(".run-activity")).toBeVisible();
  await expect(result.locator(".run-activity")).toHaveCount(0);
  await page
    .getByLabel("Message coordinator", { exact: true })
    .fill("Keep this question draft while inspecting evidence.");
  await expect(
    history.getByText("Execution details", { exact: true }),
  ).toHaveCount(0);
  await result.getByText(/^Flowfield: .*project checks passed$/).click();
  await expect(
    result.getByText("Project commands", { exact: true }),
  ).toBeVisible();
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
    `/projects/${seed.project}/tasks/INT-1/history/delivery:${seed.result}`,
  );
  await expect(
    history.getByRole("heading", { name: "Review 1 delivery", exact: true }),
  ).toBeVisible();
  await expect(history).toContainText("Approved by human");
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
  git("update-ref", "refs/heads/integration", advanced);
  await history
    .getByRole("link", { name: "Review these changes" })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Check destination" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Check destination" }).click();
  await expect(
    result.getByRole("region", { name: "Current branch checks" }),
  ).toContainText("Passed");
  await expect(
    page.getByRole("button", { name: "Check destination" }),
  ).toHaveCount(0);
  expect(git("rev-parse", "integration")).toBe(advanced);
  await expect(result).toContainText("Task complete.");
  await result.getByRole("link", { name: "Open branch check details" }).click();
  await expect(
    history.getByRole("heading", { name: "Review 1 branch checks" }),
  ).toBeVisible();
});

test("existing-project adoption previews and preserves coordinator guidance", async ({
  page,
}) => {
  const root = existingDirectory(join(state, "adoption-guidance"));
  execFileSync("git", ["init", "-b", "main", root], { stdio: "ignore" });
  const agents = join(root, "AGENTS.md");
  const original = "# Repository rules\n\nKeep our conventions.\n";
  writeFileSync(agents, original);
  writeFileSync(join(root, "AGENTS.override.md"), "Local override\n");
  await page.goto("/new-project");
  await expect(
    page.getByRole("region", { name: "Project setup instructions" }),
  ).toBeVisible();
  await expect(
    page.getByText("Add a project from the CLI", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Existing project directory")).toHaveCount(0);
  const adopted = cli(["project", "init", root]);
  await page.goto("/projects/" + adopted.id + "/edit/coordinator");
  await expect(
    page.getByRole("heading", { name: "Project guidance", exact: true }),
  ).toBeVisible();
  await page.getByText("Preview or copy guidance", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Copy skill" })).toBeVisible();
  expect(readFileSync(agents, "utf8")).toBe(original);
  await page.getByRole("button", { name: "Install project guidance" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Project guidance installed" }),
  ).toBeVisible();
  await expect(
    page.getByText(/^Untracked adoption files:.*config.toml.*guidance.json/),
  ).toContainText("Leaving them untracked blocks code delivery");
  expect(readFileSync(agents, "utf8").startsWith(original)).toBe(true);
  expect(
    existsSync(join(root, ".agents/skills/flowfield-coordinator/SKILL.md")),
  ).toBe(true);
  await expect(
    page.getByText("Instruction files and worker baseline", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Remove installed guidance", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      "Review additional instruction files: AGENTS.override.md. They may override this guidance.",
      {
        exact: true,
      },
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Start a fresh coding conversation; open sessions do not reload guidance. " +
        "Ask the harness to read AGENTS.md and the coordinator skill explicitly.",
      { exact: true },
    ),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Install project guidance" }),
  ).toBeDisabled();
  writeFileSync(agents, readFileSync(agents, "utf8") + "\nNew local rule\n");
  cli(["project", "guidance", "remove"], root);
  expect(readFileSync(agents, "utf8")).toBe(original + "\nNew local rule\n");
  expect(
    existsSync(join(root, ".agents/skills/flowfield-coordinator/SKILL.md")),
  ).toBe(false);
  await closeOverlay(page);
  await expect(page).toHaveURL(/\/projects\/adoption-guidance$/);
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
  await page.goto("/projects/input-browser/tasks/INP-1");
  const inputArea = page.getByRole("form", { name: "Task input", exact: true });
  await expect(inputArea).toContainText("Answer the worker");
  await expect(
    page.getByRole("button", { name: "Stop worker", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Reply", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Your answer", { exact: true }).fill("All records");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(page.getByRole("list", { name: "Task feed" })).toContainText(
    "All records",
  );
  await expect(
    page.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveCount(0);
  // The question loads after eligibility. An early click must wait for its answer.
  await page.route("**/questions/which-records", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.continue();
  });
  await page.goto("/projects/input-browser/inbox/which-records");
  const detail = page.getByRole("region", {
    name: "Task details",
    exact: true,
  });
  await expect(
    detail.locator(".task-conversation-header .work-state"),
  ).toHaveText("Paused");
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
  await expect(detail.locator('[data-kind="answer"]')).toContainText([
    "All records",
    "Only favourites",
  ]);
  await page.reload();
  await expect(
    detail.getByText("Only favourites", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.locator(".task-conversation-header .work-state"),
  ).toHaveText("Paused");
  const counts = await request.get(
    "/api/projects/input-browser/view/attention?column=action",
  );
  expect((await counts.json()).total).toBe(0);
  fixture("consume");
  await page.reload();
  await expect(
    detail.locator(".task-conversation-header .work-state"),
  ).toHaveText("Preparing worker");
  await expect(
    detail.getByRole("button", { name: "Edit answer", exact: true }),
  ).toHaveCount(0);
  // A running worker closes input. External correction is immutable evidence,
  // and must appear in the same task feed rather than a second question editor.
  await expect(detail.getByRole("textbox")).toHaveCount(0);
  const original = await (
    await request.get("/api/projects/input-browser/questions/which-records")
  ).json();
  const corrected = await request.post(
    "/api/projects/input-browser/questions/which-records/correction",
    {
      data: {
        expected_revision: original.revision,
        answer: "I changed my mind: all records.",
      },
    },
  );
  expect(corrected.ok()).toBe(true);
  await expect(detail.locator('[data-kind="answer"]')).toContainText([
    "All records",
    "Only favourites",
    "I changed my mind: all records.",
  ]);
});

test("inspection preserves exact versions, preview edits and direct approval", async ({
  page,
  request,
}) => {
  // Two result generations and multiple Git copies need a bounded hosted-runner budget.
  test.setTimeout(60000);
  const project = "inspection-browser";
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
  const path = `/api/projects/${project}`;
  let first: { id: string; revision: number; candidate_commit: string };
  await expect(async () => {
    const versions = await (
      await request.get(`${path}/tasks/try/results`)
    ).json();
    expect(versions.items[0]?.status).toBe("ready");
    first = versions.items[0];
  }).toPass();
  await page.route(
    "**/api/projects/inspection-browser/results/*",
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 150));
      await route.continue();
    },
  );
  const atBottom = () =>
    page
      .locator(".entity-overlay-body")
      .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3);
  for (let i = 0; i < 3; i++) {
    await page.goto(`/projects/${project}/tasks/IPV-1`);
    await expect(
      page.getByRole("region", { name: "Proposed result" }),
    ).toContainText("Ready for review");
    await expect.poll(atBottom).toBe(true);
    await expect(page.getByRole("form", { name: "Task input" })).toContainText(
      "Review Result 1",
    );
    await page
      .getByRole("button", {
        name: /^(Close editor|Back to board)$/,
        exact: true,
      })
      .click();
  }
  await page.goto(`/projects/${project}/tasks/IPV-1/changes`);
  let selectedPreview = first!.id;
  const preview = () =>
    page
      .locator(`[data-message-id="result:${selectedPreview}"]`)
      .getByRole("region", { name: "Try result", exact: true });
  await expect(
    preview()
      .locator("summary")
      .filter({ hasText: /^Try result$/ }),
  ).toBeVisible();
  expect(
    await (
      await request.get(`${path}/inspection?result_id=${first!.id}`)
    ).json(),
  ).toBeNull();
  await preview()
    .locator("summary")
    .filter({ hasText: /^Try result$/ })
    .click();
  await expect(
    preview().getByText("Run in your terminal", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Inspection", exact: true }),
  ).toHaveCount(0);
  await expect(
    preview().getByText("Inspection details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    preview().getByRole("button", { name: "Copy commands", exact: true }),
  ).toBeHidden();
  await preview().getByText("Terminal commands", { exact: true }).click();
  await expect(
    preview().getByRole("button", { name: "Copy commands", exact: true }),
  ).toBeVisible();
  const copy = await (
    await request.get(`${path}/inspection?result_id=${first!.id}`)
  ).json();
  expect(copy.commit).toBe(first!.candidate_commit);
  expect(copy.command).toMatch(/^\/bin\/sh /);
  await expect(preview().locator("pre")).toHaveText(copy.command);
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
  await approvalComment.press("Escape");
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
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
  await feedback.press("Escape");
  await expect(
    page.getByRole("region", { name: "Task details", exact: true }),
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
  await page.locator(".entity-overlay-body").evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(
    preview()
      .locator("summary")
      .filter({ hasText: /^Try result$/ }),
  ).toBeVisible();
  await preview()
    .locator("summary")
    .filter({ hasText: /^Try result$/ })
    .click();
  await expect(
    preview().getByText("IPV-1 · Review 2", { exact: true }),
  ).toBeVisible();
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
  await expect(
    page.getByText("Delivered to delivery.", { exact: false }),
  ).toBeVisible();
  await page.goto(`/projects/${project}`);
  await expect(
    page.getByRole("button", { name: "Try project", exact: true }),
  ).toHaveCount(0);
  selectedPreview = first!.id;
  await page.goto(`/projects/${project}/tasks/IPV-1/changes/${first!.id}`);
  await preview()
    .locator("summary")
    .filter({ hasText: /^Try result$/ })
    .click();
  await expect(
    preview().getByText("excluding edits in this copy.", { exact: false }),
  ).toBeVisible();
  await expect(
    preview().getByText("A newer result or run exists.", { exact: false }),
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
    await request.get(`${path}/inspection/settings`)
  ).json();
  await request.put(`${path}/inspection/settings`, {
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
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Load current run command" }).click();
  await expect(runCommand).toHaveValue("python app.py --elsewhere");
  await runCommand.fill("python app.py");
  const runSaved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`${path}/inspection/settings`) &&
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
    await request.get(`${path}/inspection/settings`)
  ).json();
  const externalUpdate = await request.put(`${path}/inspection/settings`, {
    data: {
      expected_revision: cleanSettings.revision,
      run_command: "python app.py --updated",
    },
  });
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
        await request.get(`/api/projects/outcome-${kind}/tasks/work/results`)
      ).json()
    ).items[0];
  await expect(async () =>
    expect((await result("report"))?.status).toBe("delivered"),
  ).toPass();
  // Stagger independently loaded records, including content taller than the viewport.
  let resultDelay = 0;
  let heldReport: Promise<void> | null = null;
  await page.route(
    "**/api/projects/outcome-report/results/*",
    async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      if (heldReport) await heldReport;
      body.report.summary += "\n\n" + "A detailed finding.\n\n".repeat(35);
      await new Promise((resolve) => setTimeout(resolve, resultDelay));
      await route.fulfill({ response, json: body });
    },
  );
  await page.route(
    (url) =>
      decodeURIComponent(url.pathname).includes(
        "/projects/outcome-report/tasks/work/thread/result:",
      ),
    async (route) => {
      await new Promise((resolve) =>
        setTimeout(resolve, resultDelay ? 0 : 500),
      );
      await route.continue();
    },
  );
  const reportPane = page.locator(".entity-overlay-body");
  const reportAtBottom = () =>
    reportPane.evaluate(
      (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3,
    );
  for (const delay of [0, 250]) {
    resultDelay = delay;
    await page.goto("/projects/outcome-report/inbox");
    // Use the real history card and its versioned result URL.
    await page
      .getByRole("region", { name: "History", exact: true })
      .getByRole("link", { name: /Report outcome/ })
      .click();
    await expect(
      page.getByRole("region", { name: "Worker report", exact: true }),
    ).toContainText("A detailed finding.");
    await expect.poll(reportAtBottom).toBe(true);
    await page
      .getByRole("button", {
        name: /^(Close editor|Back to board)$/,
        exact: true,
      })
      .click();
  }
  // Board opens still follow late layout, while an explicit timestamp targets the entry.
  await page.goto("/projects/outcome-report");
  await page
    .getByRole("link", { name: "ORP-1 Report outcome", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Worker report", exact: true }),
  ).toContainText("A detailed finding.");
  await expect.poll(reportAtBottom).toBe(true);
  const reportStamp = page
    .locator('[data-kind="result"]')
    .getByRole("link", { name: "Permalink: Result 1", exact: true });
  const reportHref = await reportStamp.getAttribute("href");
  let releaseReport!: () => void;
  heldReport = new Promise<void>((resolve) => {
    releaseReport = resolve;
  });
  await page.goto(reportHref!);
  await expect(
    page.getByRole("region", { name: "Proposed result", exact: true }),
  ).toBeAttached();
  await expect(reportStamp).toBeInViewport();
  const startingViewport = page.viewportSize()!;
  // A short placeholder clamps the scroll position when the viewport grows.
  // That browser adjustment must not change an explicit link into follow mode.
  await page.setViewportSize({
    ...startingViewport,
    height: startingViewport.height + 200,
  });
  releaseReport();
  heldReport = null;
  await expect(
    page.getByRole("region", { name: "Worker report", exact: true }),
  ).toContainText("A detailed finding.");
  await expect(reportStamp).toBeInViewport();
  await expect.poll(reportAtBottom).toBe(false);
  const originalViewport = startingViewport;
  await page.setViewportSize({ width: 600, height: 500 });
  await expect(reportStamp).toBeInViewport();
  await page.setViewportSize(originalViewport);
  await page.goto("/projects/outcome-report/tasks/ORP-1/changes");
  await expect(
    page.getByText(
      "Findings delivered; this does not endorse the recommendation.",
      { exact: false },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  expect((await result("report")).approved_at).toBeNull();
  await expect(async () =>
    expect((await result("partial"))?.problem_code).toBe("partial_outcome"),
  ).toPass();
  await page.goto("/projects/outcome-partial/tasks/OPT-1/changes");
  await expect(
    page.getByRole("heading", { name: "Work remaining" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Continue work", exact: true })
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
  await page.goto("/projects/outcome-checks/tasks/OCK-1/changes");
  const recovery = page.getByRole("region", { name: "Result recovery" });
  const actions = page.getByRole("group", {
    name: "Task actions",
    exact: true,
  });
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toBeVisible();
  await expect(recovery).toBeVisible();
  await expect(
    actions.getByRole("button", { name: "Archive", exact: true }),
  ).toBeVisible();
  await expect(recovery.getByRole("link")).toHaveCount(0);
  const taskPath = "/api/projects/outcome-checks/tasks/work";
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
  const currentPlan = await (await request.get(taskPath + "/stages")).json();
  expect(
    (
      await request.put(taskPath + "/stages", {
        data: {
          expected_revision: currentPlan.revision,
          agreement_revision: revisedTask.agreement_revision,
          stages: currentPlan.stages,
          reason: "Reconcile clarified outcome",
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
  await page.goto("/projects/outcome-setup/tasks/OST-1/changes");
  await actions.getByRole("link", { name: "Fix project setup" }).click();
  await expect(page).toHaveURL(/edit\/integration/);
});
