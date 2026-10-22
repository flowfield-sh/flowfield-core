import { openResource } from "./shell-navigation";
import { expect } from "@playwright/test";
import { test } from "./support";

test("workspace documents preserve drafts, exact history and restore as a new revision", async ({
  page,
  request,
}, testInfo) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Product guidance",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  const base = `/workspaces/${workspace.id}`;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base + "/tasks");
  await openResource(page, "Documents");
  await page.getByRole("link", { name: /Workspace guidance/ }).click();
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expect(page).toHaveURL(base + "/documents/guidance/edit");
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Edit document", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Content", exact: true })
    .fill(
      "# Working together\n\nCoordinate core and site here.\n\n- Keep tasks project-specific.\n- Review exact results before delivery.",
    );
  await expect(
    page.getByRole("button", { name: "Write", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Preview", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Write", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await expect(
    page.getByRole("heading", { name: "Working together", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".markdown ul").first()).toHaveCSS(
    "list-style-type",
    "disc",
  );
  await page
    .getByRole("button", { name: "Save document", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Edit", exact: true }),
  ).toBeVisible();
  const guidance = await (
    await request.get(`/api${base}/documents/guidance`)
  ).json();
  expect(guidance.revision).toBe(2);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Content", exact: true })
    .fill("My unsaved guidance");
  await page.getByRole("tab", { name: "Tasks Board", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await page
    .getByRole("tab", { name: "Workspace guidance · Edit", exact: true })
    .click();
  await request.put(`/api${base}/documents/guidance`, {
    data: {
      expected_revision: 2,
      body: "# Shared conventions\n\nUpdated elsewhere. Preserve the two-repository scope.",
    },
  });
  await expect(page.getByText(/A newer revision is available/)).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toHaveValue("My unsaved guidance");
  await page.screenshot({
    path: testInfo.outputPath("documents-conflict-desktop-light.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Load latest", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("link", { name: "Revision #2", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Working together", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Restore", exact: true }),
  ).toBeEnabled();
  await page.getByRole("link", { name: "Revision #3", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Edit", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Restore", exact: true }),
  ).toHaveCount(0);
  await page.goto(base + "/documents/guidance/revisions/3");
  await expect(
    page.getByRole("link", { name: "Edit", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Restore", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expect(page).toHaveURL(base + "/documents/guidance/edit");
  await expect(
    page.getByRole("textbox", { name: "Content", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("link", { name: "Revision #2", exact: true }).click();
  await page.screenshot({
    path: testInfo.outputPath("document-history-desktop-light.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${base}/documents/guidance$`));
  const restored = await (
    await request.get(`/api${base}/documents/guidance`)
  ).json();
  expect(restored.revision).toBe(4);
  expect(restored.restored_from).toBe(2);
  expect(restored.body).toBe(guidance.body);
  await openResource(page, "Documents");
  await expect(page).toHaveURL(`${base}/documents`);
  await expect(
    page.getByRole("link", { name: "New document", exact: true }),
  ).toHaveCount(0);
  const doc = await (
    await request.post(`/api${base}/documents`, {
      data: {
        title: "Architecture",
        body: `[Shared conventions](${base}/documents/guidance)\n\nCore owns the service. Site owns the public website.`,
      },
    })
  ).json();
  await page.goto(`${base}/documents/${doc.id}`);
  await expect(
    page.getByRole("heading", { name: "Architecture", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => localStorage.setItem("flowfield.theme", "dark"));
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Architecture", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Architecture", exact: true }),
  ).toBeInViewport();
  await page.screenshot({
    path: testInfo.outputPath("document-narrow-dark.png"),
    animations: "disabled",
  });
  await page
    .getByRole("link", { name: "Shared conventions", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`${base}/documents/guidance$`));
  await expect(
    page.getByRole("heading", { name: "Working together", exact: true }),
  ).toBeVisible();
});

test("document conversation context keeps an uncertain receipt until navigation changes", async ({
  page,
  request,
}) => {
  const workspace = await (
    await request.post("/api/workspaces", {
      data: {
        name: "Document context",
        ...Object.fromEntries(
          ["coordinator", "worker", "reviewer"].map((role) => [
            role,
            { harness: "codex", model: "test-model" },
          ]),
        ),
      },
    })
  ).json();
  const root = `/workspaces/${workspace.id}`;
  const base = `/api${root}/coordinator`;
  const conversation = await (await request.post(base)).json();
  await page.route(`**${base}{,?*}`, (route) =>
    route.fulfill({
      json: {
        welcome: "Discuss these documents.",
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
  await page.route(`**/api${root}/coordinator-settings`, (route) =>
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
  await page.route(`**/api${root}/models*`, (route) =>
    route.fulfill({
      json: [
        {
          id: "fixture",
          name: "Fixture",
          efforts: ["low"],
          modes: [{ id: "read-only", name: "Read only", description: "" }],
        },
      ],
    }),
  );
  const messages: {
    id: string;
    view_context: {
      surface: string;
      document_id: string;
      document_revision: number;
    };
  }[] = [];
  await page.route(`**${base}/messages`, (route) => {
    messages.push(route.request().postDataJSON());
    return route.fulfill({
      status: 503,
      json: { error: { code: "fixture_unavailable", message: "Retry later" } },
    });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${root}/documents/guidance`);
  await page
    .getByRole("textbox", { name: "Message coordinator" })
    .fill("Discuss this guidance");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(1);
  expect(messages[0].view_context).toMatchObject({
    surface: "document",
    document_id: "guidance",
    document_revision: 1,
  });
  await request.put(`/api${root}/documents/guidance`, {
    data: { expected_revision: 1, body: "A newer shared convention." },
  });
  await expect(
    page.getByText("A newer shared convention.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(2);
  expect(messages[1]).toEqual(messages[0]);
  await page.getByRole("link", { name: "Revision #2", exact: true }).click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => messages.length).toBe(3);
  expect(messages[2].id).not.toBe(messages[1].id);
  expect(messages[2].view_context.document_revision).toBe(2);
});
