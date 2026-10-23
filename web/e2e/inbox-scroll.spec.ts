import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, type Locator, type Page } from "@playwright/test";
import { test, state, projectWorkspace } from "./support";

test.use({
  hasTouch: true,
  launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] },
});

async function bounded(page: Page) {
  expect(
    await page.evaluate(() => ({
      height: document.documentElement.scrollHeight,
      width: document.documentElement.scrollWidth,
      top: window.scrollY,
      viewportHeight: innerHeight,
      viewportWidth: innerWidth,
    })),
  ).toEqual({
    height: page.viewportSize()!.height,
    width: page.viewportSize()!.width,
    top: 0,
    viewportHeight: page.viewportSize()!.height,
    viewportWidth: page.viewportSize()!.width,
  });
  for (const selector of [".workspace-frame", ".workspace-resource-content"]) {
    const box = (await page.locator(selector).boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  }
  expect(
    await page
      .locator(".workspace-work-content, .entity-pane-body")
      .last()
      .evaluate((node) => node.scrollWidth <= node.clientWidth),
  ).toBe(true);
}

// Wheel over the pane's padding, away from nested evidence/textareas. No
// scrollIntoView or scrollTop assignment can conceal a broken scroll owner.
async function wheel(page: Page, pane: Locator, delta: number) {
  const box = (await pane.boundingBox())!;
  await page.mouse.move(box.x + 5, box.y + box.height / 2);
  await page.mouse.wheel(0, delta);
}
async function end(page: Page, pane: Locator, bottom: boolean) {
  const distance = await pane.evaluate(
    (node, bottom) =>
      bottom
        ? node.scrollHeight - node.clientHeight - node.scrollTop
        : node.scrollTop,
    bottom,
  );
  if (distance > 1) await wheel(page, pane, bottom ? 100000 : -100000);
  await expect
    .poll(() =>
      pane.evaluate(
        (node, bottom) =>
          bottom
            ? Math.abs(node.scrollHeight - node.clientHeight - node.scrollTop)
            : node.scrollTop,
        bottom,
      ),
    )
    .toBeLessThanOrEqual(1);
  await bounded(page);
}
async function reveal(page: Page, pane: Locator, target: Locator) {
  await end(page, pane, false);
  for (let step = 0; step < 50; step++) {
    const box = await target.boundingBox();
    const frame = (await pane.boundingBox())!;
    if (box && box.y >= frame.y && box.y + box.height <= frame.y + frame.height)
      return;
    const before = await pane.evaluate((node) => node.scrollTop);
    await wheel(page, pane, 200);
    await expect
      .poll(() => pane.evaluate((node) => node.scrollTop))
      .toBeGreaterThan(before);
  }
  throw new Error("Action was not reachable with wheel scrolling");
}

for (const width of [1440, 390]) {
  test(`compact Inbox and exact response scroll independently at ${width}px`, async ({
    page,
    request,
  }, info) => {
    test.setTimeout(90000);
    async function capture(view: string) {
      for (const colorScheme of ["light", "dark"] as const) {
        await page.emulateMedia({ colorScheme });
        if (colorScheme === "dark")
          await expect(page.locator("html")).toHaveClass(/dark/);
        else await expect(page.locator("html")).not.toHaveClass(/dark/);
        await page.screenshot({
          path: info.outputPath(`${view}-${width}-${colorScheme}.png`),
          animations: "disabled",
        });
      }
    }
    const project = `compact-scroll-${width}`;
    execFileSync(resolve("../.venv/bin/python"), [
      resolve("../tests/inspection_browser_fixture.py"),
      state,
      "create",
      project,
    ]);
    const workspace = await projectWorkspace(request, project),
      base = `/workspaces/${workspace}`;
    for (let index = 0; index < 25; index++) {
      const response = await request.post(`/api${base}/questions`, {
        data: {
          id: `scope-${index}`,
          question: `Decision ${index}: which records should be exported?`,
          context: Array.from(
            { length: 12 },
            (_, i) =>
              `Context paragraph ${i}: preserve records and supporting evidence. ${"long-reference-".repeat(6)}`,
          ).join("\n\n"),
          recommendation: "Keep all records.",
          choices: ["All records", "Visible records"],
        },
      });
      expect(response.ok(), await response.text()).toBe(true);
    }
    await page.setViewportSize({ width, height: 800 });
    await page.goto(base + "/inbox");
    const pane = page.locator(".workspace-work-content"),
      inbox = page.getByRole("tabpanel", { name: "Needs me", exact: true });
    await expect(inbox.locator(".inbox-history-row")).toHaveCount(20);
    await expect(inbox.getByRole("textbox")).toHaveCount(0);
    await end(page, pane, true);
    await inbox.getByRole("button", { name: "Load more", exact: true }).click();
    await expect(inbox.locator(".inbox-history-row")).toHaveCount(26);
    const row = inbox.getByRole("link", { name: /Decision 0:/ });
    await reveal(page, pane, row);
    const saved = await pane.evaluate((node) => node.scrollTop);
    expect(saved).toBeGreaterThan(0);
    await capture("compact-inbox");
    await row.click();
    const detail = page.getByRole("region", { name: "Request", exact: true });
    const body = detail.locator(".entity-pane-body");
    await expect(
      detail.getByRole("button", { name: "Send answer", exact: true }),
    ).toBeInViewport();
    await end(page, body, true);
    await end(page, body, false);
    const answer = detail.getByRole("textbox", {
      name: "Your answer",
      exact: true,
    });
    await answer.fill("Retain this answer draft.\n".repeat(4));
    const draft = await answer.inputValue();
    await capture("request");
    await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
    await expect
      .poll(() => pane.evaluate((node) => node.scrollTop))
      .toBeCloseTo(saved, 0);
    await row.click();
    await expect(answer).toHaveValue(draft);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(
      `**/api${base}/questions/scope-0/answer`,
      async (route) => {
        await held;
        await route.fulfill({
          status: 409,
          json: {
            error: {
              code: "revision_conflict",
              message: "Question changed; review it again.",
            },
          },
        });
      },
    );
    const send = detail.getByRole("button", {
      name: "Send answer",
      exact: true,
    });
    await send.click();
    await expect(send).toBeDisabled();
    await bounded(page);
    await capture("pending");
    release();
    await expect(
      page.getByText("Question changed; review it again.").first(),
    ).toBeVisible();
    await expect(answer).toHaveValue(draft);
    await capture("stale");
    await page
      .getByRole("button", { name: "Close toast", exact: true })
      .click();
    await page.unroute(`**/api${base}/questions/scope-0/answer`);
    await send.click();
    await expect(answer).toHaveCount(0);
    await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
    await expect(inbox.locator(".inbox-history-row")).toHaveCount(25);
    await end(page, pane, true);
    await inbox.getByRole("link", { name: /Try the candidate/ }).click();
    const result = page.getByRole("region", { name: "Result", exact: true });
    const approve = result.getByRole("button", {
      name: "Approve and integrate",
      exact: true,
    });
    await expect(approve).toBeEnabled();
    await approve.click();
    await result
      .getByRole("textbox", {
        name: "Testing notes or approval comment (optional)",
      })
      .fill("Reviewed exact code.");
    await capture("approval");
    await approve.click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api${base}/view/tasks/T-1`)).json())
            .status,
        { timeout: 15000 },
      )
      .toBe("done");
    await page.getByRole("tab", { name: /^Inbox(?: \d+)?$/ }).click();
    for (const tab of ["Delivered", "History"]) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
      const panel = page.getByRole("tabpanel", { name: tab, exact: true });
      await expect(panel.locator(".inbox-history-row").first()).toBeVisible();
      await expect(panel.getByRole("textbox")).toHaveCount(0);
      await bounded(page);
      await capture(tab.toLowerCase());
    }
  });
}
