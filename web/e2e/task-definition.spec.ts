import { choose, fixtureStages } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test } from "./support";

test("long definitions collapse with a fade and history loads only when expanded", async ({
  page,
  request,
}, testInfo) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "definition-preview");
  mkdirSync(path, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path, task_prefix: "DEF" },
  });
  const body =
    Array.from(
      { length: 24 },
      (_, i) => `Paragraph ${i + 1}: a specific part of the task definition.`,
    ).join("\n\n") + "\n\n[Reference](https://example.com)";
  const task = await (
    await request.post("/api/projects/definition-preview/tasks", {
      data: { stages: fixtureStages(), title: "Read a long definition", body },
    })
  ).json();
  await page.goto(`/projects/definition-preview/tasks/${task.key}`);
  const definition = page.getByLabel("Current task definition");
  const preview = definition.locator(".definition-preview");
  const expand = definition.getByRole("button", { name: "Expand definition" });
  await expect(expand).toBeVisible();
  await expect(preview).toHaveAttribute("data-collapsed", "true");
  expect((await preview.boundingBox())!.height).toBeLessThan(240);
  await expect(preview).not.toHaveCSS("mask-image", "none");
  const initial = page.locator('[data-message-id="definition:1"]');
  await expect(initial.locator(".markdown")).toHaveCount(0);
  await initial.getByText("View definition", { exact: true }).click();
  await expect(
    initial.getByText("Paragraph 24:", { exact: false }),
  ).toBeVisible();
  await initial.getByText("View definition", { exact: true }).click();
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
  await request.put(`/api/projects/definition-preview/tasks/${task.id}`, {
    data: { expected_revision: task.revision, body: "A short replacement." },
  });
  await expect(definition).toContainText("A short replacement.");
  await expect(expand).toHaveCount(0);
  const updated = page.locator('[data-message-id="definition:2"]');
  await expect(updated.locator(".markdown")).toHaveCount(0);
  await updated.getByText("View definition changes", { exact: true }).click();
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

test("project workers have no speed setting even when the model supports Fast", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "task-speed");
  mkdirSync(path, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path, task_prefix: "SPD" },
  });
  await page.route("**/api/worker-models*", (route) =>
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
    await request.get("/api/projects/task-speed/workers")
  ).json();
  expect(settings).not.toHaveProperty("fast");
  expect(
    (
      await request.put("/api/projects/task-speed/workers", {
        data: {
          expected_revision: settings.revision,
          model: "test",
          effort: "low",
          fast: true,
        },
      })
    ).status(),
  ).toBe(422);
  await page.goto("/projects/task-speed/edit/workers");
  const form = page.getByRole("region", { name: "Worker settings" });
  await choose(form.getByLabel("Model", { exact: true }), "test");
  await expect(form.getByRole("button", { name: "Fast mode" })).toHaveCount(0);
});

test("definition revisions show focused word diffs and compact field values", async ({
  page,
  request,
}, testInfo) => {
  const project = "definition-diffs";
  mkdirSync(join(process.env.FLOWFIELD_SMOKE_STATE!, project), {
    recursive: true,
  });
  await request.post("/api/projects/initialize", {
    data: {
      path: join(process.env.FLOWFIELD_SMOKE_STATE!, project),
      task_prefix: "DIF",
    },
  });
  const task = await (
    await request.post(`/api/projects/${project}/tasks`, {
      data: {
        stages: fixtureStages(),
        title: "Search café",
        body: "Keep all rows. Preserve café labels.",
      },
    })
  ).json();
  await request.put(`/api/projects/${project}/tasks/${task.id}`, {
    data: {
      expected_revision: task.revision,
      body: "Keep selected rows. Preserve café labels.",
      task_type: "bug",
    },
  });
  await page.goto(`/projects/${project}/tasks/${task.key}`);
  const revision = page.locator('[data-message-id="definition:2"]');
  await revision.getByText("View definition changes", { exact: true }).click();
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
