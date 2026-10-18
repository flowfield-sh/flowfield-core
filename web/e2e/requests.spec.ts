import { join } from "node:path";
import { expect } from "@playwright/test";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
} from "./support";

test("individual requests retain answers and drafts across independent tabs", async ({
  page,
  request,
}, info) => {
  const project = "request-links";
  await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
    },
  });
  const workspace = await projectWorkspace(request, project);
  const base = `/workspaces/${workspace}`;
  const task = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
      {
        data: {
          title: "Export rates",
          body: "Export the agreed rates as CSV.",
          status: "in_progress",
        },
      },
    )
  ).json();
  const question = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/questions`,
      {
        data: {
          task_id: task.id,
          question: "Which columns?",
          context: "Choose the export columns",
          recommendation: "Name and rate",
        },
      },
    )
  ).json();
  const href = `${base}/requests/question/${question.id}`;
  await page.goto(base + "/inbox");
  const card = page.getByRole("article", { name: "Question: Which columns?" });
  const source = card
    .locator(":scope > header")
    .getByRole("link", { name: `${task.key}: Which columns?` });
  await expect(source).toHaveText(task.key);
  await expect(source).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(request, project)}/tasks/${task.key}`,
  );
  await card
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("Name, rate, currency");
  await card
    .getByRole("link", { name: "Open request in work tab", exact: true })
    .click();
  await expect(page).toHaveURL(href);
  const detail = page.getByRole("region", { name: "Request", exact: true });
  await expect(
    detail.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("Name, rate, currency");
  const tabs = page.getByRole("tablist", { name: "Open resources" });
  await expect(tabs.getByRole("tab", { selected: true })).toContainText(
    "T-1 Question",
  );
  await detail
    .getByRole("link", { name: "Open in work tab", exact: true })
    .click();
  await expect(tabs.getByRole("tab", { name: /T-1 Question/ })).toHaveCount(2);
  await detail
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("Name and rate");
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await expect(
    card.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveValue("Name and rate");
  await card
    .getByRole("link", { name: "Open request in work tab", exact: true })
    .click();
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(
    detail.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    detail.getByText("Name and rate", { exact: true }).first(),
  ).toBeVisible();
  await tabs.getByRole("tab", { name: /^Inbox/ }).click();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await page
    .getByRole("tabpanel", { name: "History", exact: true })
    .getByRole("link", { name: /Which columns/ })
    .click();
  await expect(page).toHaveURL(href);
  await expect(
    detail.getByRole("button", { name: "Send answer", exact: true }),
  ).toHaveCount(0);
  // Exercise explicit retry without a concurrent SSE refresh recovering first.
  await page.route("**/api/events", (route) => route.abort());
  await page.route("**/api" + href, (route) =>
    route.fulfill({
      status: 503,
      json: { error: { message: "Request temporarily unavailable" } },
    }),
  );
  await page.reload();
  await expect(
    detail.getByRole("button", { name: "Retry loading", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: info.outputPath("request-unavailable.png") });
  await page.unroute("**/api" + href);
  await detail
    .getByRole("button", { name: "Retry loading", exact: true })
    .click();
  await expect(
    detail.getByText("Name and rate", { exact: true }).first(),
  ).toBeVisible();
  const chatPath = "/api/workspaces/" + workspace + "/coordinator";
  const conversation = await (await request.post(chatPath)).json();
  await page.route("**" + chatPath + "{,?*}", (route) =>
    route.fulfill({
      json: {
        welcome: "[Review this request](" + href + ")",
        conversation,
        items: [],
        active: null,
        context: null,
        next_before: null,
        session_recovery_turn_id: null,
      },
    }),
  );
  const choice = {
    harness: "codex",
    model: "fixture",
    effort: "low",
    mode: "read-only",
    fast: false,
  };
  await page.route(
    "**/api/workspaces/" + workspace + "/coordinator-settings",
    (route) =>
      route.fulfill({
        json: {
          revision: 1,
          selection: choice,
          effective: {
            choice,
            source: "workspace",
            default_revision: 1,
            override_revision: null,
          },
        },
      }),
  );
  await page.route("**/api" + base + "/models*", (route) =>
    route.fulfill({
      json: [
        {
          id: choice.model,
          name: "Fixture",
          efforts: ["low"],
          modes: [{ id: "read-only", name: "Read only", description: "" }],
          fast: false,
        },
      ],
    }),
  );
  const messages: {
    view_context: {
      surface: string;
      request_id: string;
      request_kind: string;
    };
    task_context: unknown;
  }[] = [];
  await page.route("**" + chatPath + "/messages", (route) => {
    messages.push(route.request().postDataJSON());
    return route.fulfill({
      status: 503,
      json: {
        error: { code: "fixture_unavailable", message: "Test send captured" },
      },
    });
  });
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, project)}/tasks/${task.key}`,
  );
  await page
    .getByRole("region", { name: "Coordinator conversation" })
    .getByRole("link", { name: "Review this request", exact: true })
    .click();
  await expect(page).toHaveURL(href);
  await expect(
    detail.getByText("Name and rate", { exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Message coordinator" })
    .fill("Explain this request");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(1);
  expect(messages[0].view_context).toMatchObject({
    surface: "request",
    request_kind: "question",
    request_id: question.id,
  });
  expect(messages[0].task_context).toBeNull();
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await page.screenshot({
      path: info.outputPath(`request-${width}-${theme}.png`),
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});

test("Needs attention opens the task itself while requests remain directly accessible", async ({
  page,
  request,
}) => {
  const project = "attention-navigation";
  await fixtureProject(request, {
    data: {
      id: project,
      path: existingDirectory(join(state, project)),
    },
  });
  const workspace = await projectWorkspace(request, project);
  const base = `/workspaces/${workspace}`;
  const task = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
      {
        data: {
          title: "Build the export",
        },
      },
    )
  ).json();
  for (const id of ["earlier", "newer"])
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/questions`,
      {
        data: {
          id,
          task_id: task.id,
          question: `${id} choice`,
          context: "Choose the format",
          recommendation: "JSON",
        },
      },
    );
  await page.goto(base + "/inbox");
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  const entry = work.getByRole("link", {
    name: `${task.key} Build the export`,
    exact: true,
  });
  for (const id of ["newer", "earlier"]) {
    const href = `${base}/requests/question/${id}`;
    await expect(entry).toHaveAttribute("href", `${base}/tasks/${task.key}`);
    await entry.click();
    await expect(page).toHaveURL(`${base}/tasks/${task.key}`);
    await page.goto(href);
    const detail = page.getByRole("region", { name: "Request", exact: true });
    await expect(detail).toContainText(`${id} choice`);
    await detail
      .getByRole("textbox", { name: "Your answer", exact: true })
      .fill("JSON");
    await detail
      .getByRole("button", { name: "Send answer", exact: true })
      .click();
    await expect(
      detail.getByRole("textbox", { name: "Your answer", exact: true }),
    ).toHaveCount(0);
  }
  await expect(entry).toHaveCount(0);
  await expect(work.getByText("Needs attention", { exact: true })).toHaveCount(
    0,
  );
  await page.getByRole("link", { name: "Show all", exact: true }).click();
  await expect(page).toHaveURL(`${base}/work`);
  await expect(
    page.getByRole("tabpanel", { name: "All work", exact: true }),
  ).toBeVisible();
});
