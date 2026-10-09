import { join } from "node:path";
import { expect } from "@playwright/test";
import { choose, test, hostStatus, existingDirectory, state } from "./support";

test("Pi shares automatic discovery and preserves provider-qualified model choices", async ({
  page,
  request,
}) => {
  const project = "pi-choices";
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "PII",
        },
      })
    ).ok(),
  ).toBe(true);
  await page.route("**/api/harnesses", (route) =>
    route.fulfill({
      json: [
        hostStatus("codex", false),
        hostStatus("claude-code", false),
        hostStatus("pi"),
      ],
    }),
  );
  let release!: () => void;
  const loaded = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/worker-models*", async (route) => {
    expect(new URL(route.request().url()).searchParams.get("harness")).toBe(
      "pi",
    );
    await loaded;
    await route.fulfill({
      json: ["first", "second"].map((provider) => ({
        id: `${provider}/same-model`,
        name: `Same model · ${provider}`,
        efforts: ["off", "low"],
        modes: [{ id: "full-access", name: "Full access" }],
        fast: false,
      })),
    });
  });
  let selection: unknown = null;
  await page.route(
    `**/api/projects/${project}/coordinator-settings`,
    async (route) => {
      if (route.request().method() === "PUT")
        selection = route.request().postDataJSON().selection;
      await route.fulfill({
        json: {
          revision: selection ? 2 : 1,
          selection,
          effective: selection
            ? {
                choice: selection,
                source: "project",
                default_revision: 2,
                override_revision: null,
                registration: null,
              }
            : null,
        },
      });
    },
  );
  await page.goto(`/projects/${project}`);
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
    exact: true,
  });
  await expect(picker).toBeVisible();
  await expect(picker.getByLabel("Model", { exact: true })).toBeDisabled();
  await expect(
    picker.getByRole("combobox", { name: "Harness", exact: true }),
  ).toContainText("Pi");
  await expect(picker.locator(".harness-logo")).toHaveCount(1);
  release();
  await choose(
    picker.getByLabel("Model", { exact: true }),
    "second/same-model",
  );
  await choose(picker.getByLabel("Reasoning effort"), "low");
  await choose(picker.getByLabel("Access mode"), "full-access");
  await picker.getByRole("button", { name: "Save", exact: true }).click();
  await expect(picker).not.toBeVisible();
  expect(selection).toMatchObject({
    harness: "pi",
    model: "second/same-model",
    effort: "low",
    mode: "full-access",
  });
});
