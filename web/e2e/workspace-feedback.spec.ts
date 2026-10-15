import { expect } from "@playwright/test";
import { join } from "node:path";
import {
  test,
  fixtureProject,
  existingDirectory,
  state,
  projectWorkspace,
  fixtureStages,
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
      task_prefix: "FDB",
    },
  });
  const workspace = await projectWorkspace(request, project),
    base = `/workspaces/${workspace}`;
  for (const id of ["first", "second"])
    await request.post(`/api/projects/${project}/tasks`, {
      data: { id, title: id, stages: fixtureStages() },
    });
  await page.goto(base);
  const work = page.getByRole("navigation", { name: "Work", exact: true });
  await expect(work.getByRole("link").first()).toHaveAttribute(
    "href",
    `/projects/${project}/tasks/FDB-1`,
  );
  await request.post(`/api/projects/${project}/questions`, {
    data: {
      id: "attention",
      task_id: "second",
      question: "Choose the format",
      context: "A decision is needed",
      recommendation: "JSON",
    },
  });
  await expect(work.getByRole("link").first()).toHaveAttribute(
    "href",
    `/projects/${project}/tasks/FDB-2`,
  );
  await expect(
    work.getByText("Needs attention", { exact: true }),
  ).toBeVisible();
  const resources = page.getByRole("navigation", {
    name: "Workspace resources",
  });
  for (const name of ["Projects", "Documents", "Milestones"])
    await resources.getByRole("link", { name, exact: true }).click();
  const tabs = page.getByRole("tablist", { name: "Open resources" });
  const titles = () =>
    tabs
      .getByRole("tab")
      .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("aria-label")));
  const before = await titles();
  const milestones = tabs.getByRole("tab", { name: "Milestones", exact: true });
  await milestones.focus();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect
    .poll(titles)
    .toEqual([...before.slice(0, -2), before.at(-1), before.at(-2)]);
  const projects = tabs.getByRole("tab", { name: "Projects", exact: true });
  const target = await milestones.boundingBox(),
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
    .toEqual([before[0], before[1], "Milestones", "Projects", before.at(-2)]);
  await page.reload();
  await expect
    .poll(titles)
    .toEqual([before[0], before[1], "Milestones", "Projects", before.at(-2)]);
  await expect(tabs.getByRole("tab").first()).toHaveAccessibleName("Inbox 1");
  await resources
    .getByRole("link", { name: "Workspace settings", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Workspace settings", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
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
  await page
    .getByRole("button", { name: "New workspace", exact: true })
    .hover();
  await expect(
    page.getByRole("tooltip", { name: "New workspace", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "New workspace", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "New workspace", exact: true }),
  ).toBeVisible();
  await expect(
    tabs.getByRole("tab", { name: "New workspace", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(
    page.locator(".project-list").getByRole("link", { name: /Feedback shell/ }),
  ).toContainText(/1\s*in Inbox/);
});
