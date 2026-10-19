import { expect } from "@playwright/test";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
  checkout,
} from "./support";

test("attention leads Work and Home; resource tabs reorder and host settings", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1540, height: 1000 });
  const project = "feedback-shell";
  await fixtureProject(request, {
    data: {
      id: project,
      name: "Feedback shell",
      path: existingDirectory(join(state, project)),
    },
  });
  const workspace = await projectWorkspace(request, project),
    base = `/workspaces/${workspace}`;
  for (const id of ["first", "second"])
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
      {
        data: { id, title: id },
      },
    );
  await page.goto(base);
  await expect(page).toHaveURL(base + "/inbox");
  await expect(
    page.getByRole("tab", { name: "Needs me", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "Workspace resources" })
    .getByRole("link", { name: "Tasks Board", exact: true })
    .click();
  await expect(page).toHaveURL(base + "/tasks");
  // Saved filtered links retain their collection semantics after the landing change.
  await page.goto(base + "?project=" + project);
  await expect(page).toHaveURL(base + "/tasks?project=" + project);
  await expect(page.getByLabel("Filter by project")).toHaveValue(project);
  const work = page.getByRole("navigation", {
    name: "Recent Work",
    exact: true,
  });
  await expect(work.getByRole("link")).toHaveCount(0);
  await request.post(
    `/api/workspaces/${await projectWorkspace(request, project)}/questions`,
    {
      data: {
        id: "attention",
        task_id: "second",
        question: "Choose the format",
        context: "A decision is needed",
        recommendation: "JSON",
      },
    },
  );
  await expect(work.getByRole("link").first()).toHaveAttribute(
    "href",
    `/workspaces/${await projectWorkspace(request, project)}/requests/question/attention`,
  );
  await expect(
    work.getByText("Needs attention", { exact: true }),
  ).toBeVisible();
  const resources = page.getByRole("navigation", {
    name: "Workspace resources",
  });
  await expect(resources.getByRole("link")).toHaveText([
    "Projects",
    "Tasks Board",
    "Documents",
    "Files",
    "Archive",
    "Settings",
  ]);
  await page
    .getByRole("link", { name: "Flowfield settings", exact: true })
    .hover();
  await expect(page.getByRole("tooltip")).toHaveText("Global settings");
  for (const name of ["Projects", "Documents", "Files"])
    await resources.getByRole("link", { name, exact: true }).click();
  const tabs = page.getByRole("tablist", { name: "Open resources" });
  // The Inbox count loads independently of tab ordering.
  await expect(tabs.getByRole("tab").first()).toHaveAccessibleName("Inbox 1");
  const titles = () =>
    tabs
      .getByRole("tab")
      .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("aria-label")));
  const before = await titles();
  const files = tabs.getByRole("tab", { name: "Files", exact: true });
  await files.focus();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect
    .poll(titles)
    .toEqual([...before.slice(0, -2), before.at(-1), before.at(-2)]);
  const projects = tabs.getByRole("tab", { name: "Projects", exact: true });
  const target = await files.boundingBox(),
    source = await projects.boundingBox();
  await page.mouse.move(
    source!.x + source!.width / 2,
    source!.y + source!.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    target!.x + target!.width - 5,
    target!.y + target!.height / 2,
    { steps: 20 },
  );
  await page.mouse.up();
  await expect
    .poll(titles)
    .toEqual([...before.slice(0, -3), "Files", "Projects", before.at(-2)]);
  await page.reload();
  await expect
    .poll(titles)
    .toEqual([...before.slice(0, -3), "Files", "Projects", before.at(-2)]);
  await expect(tabs.getByRole("tab").first()).toHaveAccessibleName("Inbox 1");
  await resources.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Workspace settings", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Flowfield settings", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await expect(
    tabs.getByRole("tab", { name: "Flowfield settings", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("textbox", { name: "Message coordinator", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await expect(
    page.locator(".project-list").getByRole("link", { name: /Feedback shell/ }),
  ).toContainText(/1\s*in Inbox/);
});

test("failed intervention offers recovery and leaves Needs me immediately after queued retry", async ({
  page,
  request,
}, testInfo) => {
  const seed = JSON.parse(
    execFileSync(
      join(checkout, ".venv/bin/python"),
      [join(checkout, "tests/inbox_retry_browser_fixture.py"), state],
      { encoding: "utf8" },
    ),
  );
  const workspace = await projectWorkspace(request, seed.project);
  await page.goto(`/workspaces/${workspace}/inbox`);
  const card = page.getByRole("article", {
    name: "Intervention: Recover the import",
    exact: true,
  });
  await expect(card).toBeVisible();
  await expect(card).not.toContainText("no longer awaiting a response");
  await expect(
    card.getByRole("button", { name: "Retry worker", exact: true }),
  ).toBeVisible();
  await expect(card.locator(":scope > header").getByRole("img")).toHaveCount(1);
  await page.screenshot({
    path: testInfo.outputPath("intervention-inbox.png"),
    animations: "disabled",
  });
  const padding = await card.locator(".task-input").evaluate((footer) => {
    const button = footer.querySelector("button")!.getBoundingClientRect();
    const box = footer.getBoundingClientRect();
    return {
      above:
        button.top -
        box.top -
        parseFloat(getComputedStyle(footer).borderTopWidth),
      below: box.bottom - button.bottom,
    };
  });
  expect(Math.abs(padding.above - padding.below)).toBeLessThan(1);
  await card.getByRole("button", { name: "Retry worker", exact: true }).click();
  await expect(card).toHaveCount(0);
  await expect(page.getByText("Nothing needs your attention.")).toBeVisible();
  const action = await (
    await request.get(
      `/api/workspaces/${workspace}/view/attention?column=action`,
    )
  ).json();
  expect(action.total).toBe(0);
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.locator(".inbox-history")).toContainText(
    "Recover the import",
  );
  await page.reload();
  await expect(page.locator(".inbox-history")).toContainText(
    "Recover the import",
  );
});
