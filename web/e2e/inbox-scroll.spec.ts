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
  for (const selector of [
    ".workspace-frame",
    ".workspace-resource-content",
    ".workspace-work-content",
  ]) {
    const box = (await page.locator(selector).boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  }
  expect(
    await page
      .locator(".workspace-work-content")
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
  test(`mixed Inbox scroll ownership and reachable responses at ${width}px`, async ({
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
    const project = `mixed-inbox-scroll-${width}`;
    execFileSync(resolve("../.venv/bin/python"), [
      resolve("../tests/inspection_browser_fixture.py"),
      state,
      "create",
      project,
    ]);
    const workspace = await projectWorkspace(request, project);
    const base = `/api/workspaces/${workspace}`;
    const response = await request.post(`${base}/questions`, {
      data: {
        id: "scope",
        question: "Which records should be exported?",
        context: Array.from(
          { length: 12 },
          (_, i) =>
            `Context paragraph ${i}: preserve the records and review their supporting evidence. ${"long-reference-".repeat(6)}`,
        ).join("\n\n"),
        recommendation: "Keep all records.",
        choices: ["All records", "Visible records"],
      },
    });
    expect(response.ok(), await response.text()).toBe(true);
    // Keep real result/revision/approval bindings; enlarge only displayed evidence.
    await page.route(`**${base}/integrations/*`, async (route) => {
      const response = await route.fetch();
      const record = await response.json();
      await route.fulfill({
        json: {
          ...record,
          checks: record.checks.map((check: object) => ({
            ...check,
            output: Array.from(
              { length: 90 },
              (_, i) => `Evidence line ${i}: captured check output`,
            ).join("\n"),
          })),
        },
      });
    });
    await page.setViewportSize({ width, height: 800 });
    await page.emulateMedia({ colorScheme: width === 390 ? "dark" : "light" });
    await page.goto(`/workspaces/${workspace}/inbox`);
    const pane = page.locator(".workspace-work-content");
    const question = page.getByRole("article", { name: /^Question:/ });
    const result = page.getByRole("article", { name: /^Result:/ });
    const answer = question.getByRole("textbox", {
      name: "Your answer",
      exact: true,
    });
    const approve = result.getByRole("button", {
      name: "Approve and integrate",
      exact: true,
    });
    await expect(page.getByRole("article")).toHaveCount(2);
    await expect(approve).toBeEnabled();
    // Preview preparation replaces a short pending row with its launcher.
    // Establish the completed initial layout before measuring wheel endpoints.
    await expect(
      result.getByRole("textbox", { name: "Terminal commands", exact: true }),
    ).toBeVisible();
    // Assert before focus/click can scroll the document back and conceal overflow.
    await bounded(page);
    await reveal(page, pane, answer);
    await answer.fill("Retain this answer draft.\n".repeat(10));
    const draft = await answer.inputValue();
    await bounded(page);
    // Inspect cmdk's actual generated label and response portal containment.
    expect(
      await answer.evaluate((node) => {
        const label = node
          .closest("[cmdk-root]")!
          .querySelector("[cmdk-label]") as HTMLElement;
        const scroller = node.closest(".workspace-work-content")!;
        return !!label.offsetParent && scroller.contains(label.offsetParent);
      }),
    ).toBe(true);
    const fixed = page.locator(".workspace-tab-strip");
    const fixedBox = await fixed.boundingBox();
    const composerBox = await page
      .locator(".coordinator-composer")
      .boundingBox();
    const footerBox =
      width === 1440
        ? await page.locator('[data-sidebar="footer"]').boundingBox()
        : null;
    await end(page, pane, true);
    await expect(approve).toBeInViewport();
    expect(await fixed.boundingBox()).toEqual(fixedBox);
    expect(await page.locator(".coordinator-composer").boundingBox()).toEqual(
      composerBox,
    );
    if (width === 1440)
      expect(
        await page.locator('[data-sidebar="footer"]').boundingBox(),
      ).toEqual(footerBox);
    await approve.click();
    const notes = result.getByRole("textbox", {
      name: "Testing notes or approval comment (optional)",
    });
    await notes.fill("Keep this approval draft.");
    for (const name of [
      /Flowfield:.*project checks passed/,
      /Worker’s test report/,
      /^Code changes$/,
    ]) {
      const summary = result.locator("summary").filter({ hasText: name });
      await reveal(page, pane, summary);
      await summary.click();
      await bounded(page);
    }
    const command = result
      .locator("summary")
      .filter({ hasText: "python app.py" });
    await reveal(page, pane, command);
    await command.click();
    const evidence = result.locator(".evidence-output");
    await expect(evidence).toContainText("Evidence line 89");
    await reveal(page, pane, command);
    await wheel(
      page,
      pane,
      (await command.boundingBox())!.y - (await pane.boundingBox())!.y - 20,
    );
    await expect
      .poll(() => command.boundingBox().then((box) => box!.y))
      .toBeLessThan((await pane.boundingBox())!.y + 50);
    const evidenceBox = (await evidence.boundingBox())!;
    const paneBox = (await pane.boundingBox())!;
    await page.mouse.move(
      evidenceBox.x + evidenceBox.width / 2,
      Math.max(paneBox.y + 30, evidenceBox.y + 30),
    );
    const parentTop = await pane.evaluate((node) => node.scrollTop);
    await page.mouse.wheel(0, 10000);
    await expect
      .poll(() =>
        evidence.evaluate(
          (node) => node.scrollHeight - node.clientHeight - node.scrollTop,
        ),
      )
      .toBeLessThanOrEqual(1);
    expect(await pane.evaluate((node) => node.scrollTop)).toBe(parentTop);
    await page.mouse.wheel(0, -10000);
    await expect
      .poll(() => evidence.evaluate((node) => node.scrollTop))
      .toBe(0);
    await bounded(page);
    await capture("evidence");
    // Real keyboard scrolling from a focused disclosure reaches both ends.
    await command.focus();
    await page.keyboard.press("End");
    await expect
      .poll(() =>
        pane.evaluate(
          (node) => node.scrollHeight - node.clientHeight - node.scrollTop,
        ),
      )
      .toBeLessThanOrEqual(1);
    await expect(approve).toBeInViewport();
    await capture("responses");
    await page.keyboard.press("Home");
    await expect.poll(() => pane.evaluate((node) => node.scrollTop)).toBe(0);
    await bounded(page);
    if (width === 1440) {
      const divider = page.getByRole("separator", {
        name: "Resize coordinator and work",
      });
      const box = (await divider.boundingBox())!;
      const before = (await pane.boundingBox())!.width;
      await page.mouse.move(box.x, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 70, box.y + box.height / 2, { steps: 5 });
      await page.mouse.up();
      await expect
        .poll(() => pane.boundingBox().then((box) => box!.width))
        .toBeLessThan(before - 50);
    } else {
      // Chromium's touch input drives the browser's native scrolling pipeline.
      const session = await page.context().newCDPSession(page);
      const box = (await pane.boundingBox())!;
      await session.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [{ x: box.x + 8, y: box.y + box.height - 60 }],
      });
      for (let distance = 30; distance <= 300; distance += 30)
        await session.send("Input.dispatchTouchEvent", {
          type: "touchMove",
          touchPoints: [
            { x: box.x + 8, y: box.y + box.height - 60 - distance },
          ],
        });
      await session.send("Input.dispatchTouchEvent", {
        type: "touchEnd",
        touchPoints: [],
      });
      await expect
        .poll(() => pane.evaluate((node) => node.scrollTop))
        .toBeGreaterThan(0);
      const afterSwipe = await pane.evaluate((node) => node.scrollTop);
      await session.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [{ x: box.x + 8, y: box.y + 60 }],
      });
      for (let distance = 30; distance <= 300; distance += 30)
        await session.send("Input.dispatchTouchEvent", {
          type: "touchMove",
          touchPoints: [{ x: box.x + 8, y: box.y + 60 + distance }],
        });
      await session.send("Input.dispatchTouchEvent", {
        type: "touchEnd",
        touchPoints: [],
      });
      await expect
        .poll(() => pane.evaluate((node) => node.scrollTop))
        .toBeLessThan(afterSwipe);
      await bounded(page);
      await session.detach();
      await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
      await expect(page.locator(".coordinator-composer")).toBeVisible();
      await page.screenshot({
        path: info.outputPath("coordinator-narrow.png"),
      });
      await page.getByRole("tab", { name: "Work", exact: true }).click();
    }
    await expect(answer).toHaveValue(draft);
    await expect(notes).toHaveValue("Keep this approval draft.");
    const openResult = result.getByRole("link", {
      name: "Open request in work tab",
    });
    await reveal(page, pane, openResult);
    const saved = await pane.evaluate((node) => node.scrollTop);
    expect(saved).toBeGreaterThan(0);
    // Resource switches must preserve the existing scroll/draft owner.
    await openResult.click();
    await expect(page.locator(".entity-pane")).toBeVisible();
    await boundedDetail(page);
    await page.getByRole("tab", { name: /^Inbox/ }).click();
    await expect(answer).toHaveValue(draft);
    await expect
      .poll(() => pane.evaluate((node) => node.scrollTop))
      .toBeCloseTo(saved, 0);
    await end(page, pane, false);
    await capture("mixed");
    // A pending answer and a stale response keep the same reachable form/draft.
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**${base}/questions/scope/answer`, async (route) => {
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
    });
    const send = question.getByRole("button", {
      name: "Send answer",
      exact: true,
    });
    await reveal(page, pane, send);
    await send.click();
    await expect(send).toBeDisabled();
    await bounded(page);
    await capture("pending");
    release();
    await expect(
      page.getByText("Question changed; review it again.").first(),
    ).toBeVisible();
    await expect(answer).toHaveValue(draft);
    await bounded(page);
    await capture("stale");
    await page
      .getByRole("button", { name: "Close toast", exact: true })
      .click();
    await page.unroute(`**${base}/questions/scope/answer`);
    await send.click();
    await expect(question).toHaveCount(0);
    await bounded(page);
    await end(page, pane, true);
    await capture("single");
    // One item, then empty, then actual Delivered and History records.
    await approve.click();
    await expect(result).toHaveCount(0, { timeout: 15000 });
    await expect
      .poll(
        async () =>
          (await (await request.get(`${base}/view/tasks/T-1`)).json()).status,
        { timeout: 15000 },
      )
      .toBe("done");
    await expect(page.getByText("Nothing needs your attention.")).toBeVisible();
    await bounded(page);
    await capture("empty");
    for (const tab of ["Delivered", "History"]) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
      await expect(
        page.getByRole("tabpanel", { name: tab, exact: true }),
      ).toBeVisible();
      const panel = page.getByRole("tabpanel", { name: tab, exact: true });
      if (tab === "Delivered") {
        await expect(panel.getByRole("article")).toHaveCount(1);
        await expect(
          panel.getByRole("link", { name: "Access results", exact: true }),
        ).toBeVisible();
      } else {
        await expect(
          panel.getByRole("link", { name: /Try the candidate/ }),
        ).toBeVisible();
        await expect(
          panel.getByRole("link", { name: /Which records should be exported/ }),
        ).toBeVisible();
      }
      await bounded(page);
      await capture(tab.toLowerCase());
    }
  });
}

async function boundedDetail(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(
    page.viewportSize()!.height,
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    page.viewportSize()!.width,
  );
}
