import { state } from "./support";
import { fixtureProject, projectWorkspace, choose, test } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";

test("long definitions collapse with a fade and history loads only when expanded", async ({
  page,
  request,
}, testInfo) => {
  const path = join(state, "definition-preview");
  mkdirSync(path, { recursive: true });
  await fixtureProject(request, {
    data: { path },
  });
  const body =
    Array.from(
      { length: 24 },
      (_, i) => `Paragraph ${i + 1}: a specific part of the task definition.`,
    ).join("\n\n") + "\n\n[Reference](https://example.com)";
  const task = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, "definition-preview")}/tasks`,
      {
        data: { title: "Read a long definition", body },
      },
    )
  ).json();
  await page.goto(
    `/workspaces/${await projectWorkspace(page.request, "definition-preview")}/tasks/${task.key}`,
  );
  const definition = page.getByLabel("Current task definition");
  const preview = definition.locator(".definition-preview");
  const expand = definition.getByRole("button", { name: "Expand definition" });
  await expect(expand).toBeVisible();
  await expect(preview).toHaveAttribute("data-collapsed", "true");
  expect((await preview.boundingBox())!.height).toBeLessThan(240);
  await expect(preview).not.toHaveCSS("mask-image", "none");
  await expand.click();
  const collapse = definition.getByRole("button", {
    name: "Collapse definition",
  });
  await expect(collapse).toHaveAttribute("aria-expanded", "true");
  await expect(preview).toHaveCSS("mask-image", "none");
  expect(
    await definition.evaluate(
      (node) =>
        node.querySelector(".definition-toggle")!.getBoundingClientRect().top >=
        node.querySelector(".definition-preview")!.getBoundingClientRect()
          .bottom,
    ),
  ).toBe(true);
  await collapse.click();
  await expect(expand).toBeVisible();
  await expect(
    definition.getByText("Definition", { exact: true }),
  ).toBeInViewport();
  await page.screenshot({
    path: testInfo.outputPath("definition-collapsed.png"),
  });
  await preview.getByRole("link", { name: "Reference" }).focus();
  await expect(collapse).toHaveAttribute("aria-expanded", "true");
  await expect(preview).toHaveCSS("mask-image", "none");
  await collapse.click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(expand).toBeVisible();
  await expand.click();
  await collapse.click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await request.put(
    `/api/workspaces/${await projectWorkspace(request, "definition-preview")}/tasks/${task.id}`,
    {
      data: { expected_revision: task.revision, body: "A short replacement." },
    },
  );
  await expect(definition).toContainText("A short replacement.");
  await expect(expand).toHaveCount(0);
  const initial = page.locator('[data-message-id="definition:1"]');
  await expect(initial.locator(".markdown")).toHaveCount(0);
  await initial.locator(".activity-entry > summary").click();
  await expect(
    initial.getByText("View definition", { exact: true }),
  ).toHaveCount(0);
  await expect(
    initial.getByText("Paragraph 24:", { exact: false }),
  ).toBeVisible();
  await initial.locator(".activity-entry > summary").click();
  const updated = page.locator('[data-message-id="definition:2"]');
  await expect(updated.locator(".markdown")).toHaveCount(0);
  await updated.locator(".activity-entry > summary").click();
  await expect
    .poll(() =>
      updated.locator(".text-changes").evaluateAll((nodes) =>
        nodes
          .map((node) => {
            const copy = node.cloneNode(true) as HTMLElement;
            copy.querySelectorAll("ins, .sr-only").forEach((el) => el.remove());
            return copy.textContent;
          })
          .join(" "),
      ),
    )
    .toContain("Paragraph 24");
  await expect
    .poll(() =>
      updated.locator(".text-changes").evaluateAll((nodes) =>
        nodes
          .map((node) => {
            const copy = node.cloneNode(true) as HTMLElement;
            copy.querySelectorAll("del, .sr-only").forEach((el) => el.remove());
            return copy.textContent;
          })
          .join(" "),
      ),
    )
    .toContain("A short replacement.");
});

test("workspace workers reject flattened role fields and show the shared Fast checkbox", async ({
  page,
  request,
}) => {
  const path = join(state, "task-speed");
  mkdirSync(path, { recursive: true });
  await fixtureProject(request, {
    data: { path },
  });
  const workspace = await projectWorkspace(request, "task-speed");
  await page.route(`**/api/workspaces/${workspace}/models*`, (route) =>
    route.fulfill({
      json: [
        {
          id: "test",
          name: "Test",
          efforts: ["low"],
          modes: [],
          fast: true,
          fast_description: "Increased usage",
        },
      ],
    }),
  );
  const settings = await (
    await request.get(`/api/workspaces/${workspace}/workers`)
  ).json();
  expect(settings).not.toHaveProperty("fast");
  expect(
    (
      await request.put(`/api/workspaces/${workspace}/workers`, {
        data: {
          expected_revision: settings.revision,
          model: "test",
          effort: "low",
          fast: true,
        },
      })
    ).status(),
  ).toBe(422);
  await page.goto(`/workspaces/${workspace}/edit/workers`);
  const form = page.getByRole("region", { name: "Worker settings" });
  await choose(form.getByLabel("Harness", { exact: true }), "codex");
  await choose(form.getByLabel("Model", { exact: true }), "test");
  await expect(form.getByRole("checkbox", { name: "Fast mode" })).toBeVisible();
  await expect(
    form.getByRole("checkbox", { name: "Fast mode" }),
  ).not.toBeChecked();
});

test("definition revisions show focused word diffs and compact field values", async ({
  page,
  request,
}, testInfo) => {
  const project = "definition-diffs";
  mkdirSync(join(state, project), {
    recursive: true,
  });
  await fixtureProject(request, {
    data: {
      path: join(state, project),
    },
  });
  const task = await (
    await request.post(
      `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
      {
        data: {
          title: "Search café",
          labels: ["feature"],
          body: "Keep all rows. Preserve café labels.",
        },
      },
    )
  ).json();
  await request.put(
    `/api/workspaces/${await projectWorkspace(request, project)}/tasks/${task.id}`,
    {
      data: {
        expected_revision: task.revision,
        body: "Keep selected rows. Preserve café labels.",
        labels: ["bug"],
      },
    },
  );
  await page.goto(
    `/workspaces/${await projectWorkspace(request, project)}/tasks/${task.key}/history`,
  );
  const revision = page.locator('[data-message-id="definition:2"]');
  await revision.locator(".activity-entry > summary").click();
  await expect(revision.locator("del")).toHaveText("all");
  await expect(revision.locator("ins")).toHaveText("selected");
  await expect(revision.locator(".text-changes")).toContainText(
    "Preserve café labels.",
  );
  await expect(revision.locator(".field-change")).toContainText(
    "Before: Feature",
  );
  await expect(revision.locator(".field-change")).toContainText("After: Bug");
  await page.screenshot({
    path: testInfo.outputPath("definition-diff.png"),
    animations: "disabled",
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass("dark");
  await page.screenshot({
    path: testInfo.outputPath("definition-diff-dark.png"),
    animations: "disabled",
  });
});
