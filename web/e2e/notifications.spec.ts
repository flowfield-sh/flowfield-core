import { fixtureStages } from "./support";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import { test, existingDirectory, state, stubModelCatalog } from "./support";

test("queue errors show a toast with a settings link and retain notification history", async ({
  page,
  request,
}) => {
  const project = "queue-notices";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "NTF" },
  });
  await page.goto(`/projects/${project}`);
  await page.getByRole("button", { name: "Run queue", exact: true }).click();
  const notices = page.getByRole("dialog", {
    name: "Notifications",
    exact: true,
  });
  await expect(notices).toHaveCount(0);
  const failure = page.locator("[data-sonner-toast][data-type=error]");
  await expect(failure).toContainText("Queue could not start");
  await expect(failure).toContainText("Choose a worker harness and model");
  await page.getByRole("button", { name: /Notifications/ }).click();
  await expect(notices).toBeVisible();
  await expect.poll(async () => (await failure.boundingBox())?.y).toBe(24);
  const bounds = (await failure.boundingBox())!;
  expect(bounds.x + bounds.width).toBe(page.viewportSize()!.width - 24);
  await expect(notices).not.toContainText(
    "Messages from this browser session.",
  );
  await expect(notices).not.toContainText(
    "Notify when your input is needed while",
  );
  await expect(
    notices.getByRole("heading", {
      name: "Queue could not start",
      exact: true,
    }),
  ).toBeVisible();
  await expect(notices).toContainText(/model/i);
  await expect(page.locator(".queue-controls [role=alert]")).toHaveCount(0);

  await notices.getByRole("button", { name: "Close", exact: true }).click();
  await failure
    .getByRole("link", { name: "Worker settings", exact: true })
    .click();
  await expect(notices).toHaveCount(0);
  await expect(page).toHaveURL(
    new RegExp(`/projects/${project}/edit/workers$`),
  );
  await expect(
    page.getByRole("tab", { name: "Workers", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Notifications/ }).click();
  await expect(
    notices.getByRole("heading", {
      name: "Queue could not start",
      exact: true,
    }),
  ).toBeVisible();
  await notices
    .getByRole("button", { name: "Clear notifications", exact: true })
    .click();
  await expect(
    notices.getByText("No notifications.", { exact: true }),
  ).toBeVisible();
});

test("browser notifications are opt-in, deduplicate across tabs and link to a task", async ({
  page,
  request,
  context,
}) => {
  await context.addInitScript(() => {
    const notices: {
      title: string;
      options: NotificationOptions;
      onclick?: () => void;
      close: () => void;
    }[] = [];
    Object.defineProperty(window, "demoNotices", { value: notices });
    class TestNotification {
      static permission = "granted";
      static async requestPermission() {
        this.permission = "granted";
        return "granted";
      }
      constructor(
        public title: string,
        public options: NotificationOptions,
      ) {
        notices.push(this);
      }
      close() {}
    }
    Object.defineProperty(window, "Notification", { value: TestNotification });
    Object.defineProperty(document, "visibilityState", { get: () => "hidden" });
  });
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "notify-project")),
      task_prefix: "BNF",
    },
  });
  await request.post("/api/projects/notify-project/tasks", {
    data: { stages: fixtureStages(), id: "notice", title: "Needs a choice" },
  });
  await page.goto("/projects/notify-project");
  await page.getByRole("button", { name: /Notifications/ }).click();
  await page
    .getByRole("button", { name: "Enable browser notifications", exact: true })
    .click();
  await expect
    .poll(() =>
      request
        .get("/api/notifications/settings")
        .then(async (r) => (await r.json()).browser_enabled),
    )
    .toBe(true);
  await request.post("/api/projects/notify-project/questions", {
    data: {
      id: "notify-choice",
      task_id: "notice",
      question: "Which behavior?",
      context: "A meaningful choice.",
      recommendation: "Keep it small.",
      blocking_scope: "Behavior",
    },
  });
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            (window as unknown as { demoNotices: unknown[] }).demoNotices
              .length,
        ),
      { timeout: 10000 },
    )
    .toBe(1);
  const second = await context.newPage();
  const secondRead = second.waitForResponse((response) =>
    response.url().endsWith("/api/notifications/browser/claim"),
  );
  await second.goto("/projects/notify-project");
  await secondRead;
  expect(
    await second.evaluate(
      () =>
        (window as unknown as { demoNotices: unknown[] }).demoNotices.length,
    ),
  ).toBe(0);
  await expect
    .poll(async () =>
      (await (await request.get("/api/notifications")).json()).items.some(
        (item: { key: string }) => item.key.includes("notify-choice"),
      ),
    )
    .toBe(true);
  expect(
    await second.evaluate(() =>
      localStorage.getItem("flowfield-attention-receipts"),
    ),
  ).toBeNull();
  await page.evaluate(() =>
    (
      window as unknown as { demoNotices: { onclick: () => void }[] }
    ).demoNotices[0].onclick(),
  );
  await expect(page).toHaveURL(
    /tasks\/BNF-1\/conversation\/question:notify-choice:1/,
  );
  await page.reload();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { demoNotices: unknown[] }).demoNotices.length,
      ),
    )
    .toBe(0);
  await second.close();
});

test("update notifications persist, dismiss across browsers and share manual discovery", async ({
  page,
  request,
  browser,
}) => {
  const observe = (version: string) =>
    execFileSync("uv", [
      "run",
      "--project",
      "..",
      "../tests/notification_browser_fixture.py",
      state,
      version,
    ]);
  observe("9.9.1");
  let manualChecks = 0;
  let startups = 0;
  await page.route("**/api/updates/check", async (route) => {
    if (route.request().postDataJSON().reason === "manual") {
      manualChecks++;
      observe(manualChecks === 1 ? "9.9.1" : "9.9.2");
    } else startups++;
    await route.fulfill({
      json: await (await request.get("/api/updates")).json(),
    });
  });
  await page.goto("/");
  await expect.poll(() => startups).toBeGreaterThan(0);
  await expect(
    page.getByRole("dialog", { name: "Notifications", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /Notifications/ }).click();
  const sheet = page.getByRole("dialog", {
    name: "Notifications",
    exact: true,
  });
  const notice = sheet
    .getByRole("alert")
    .filter({ hasText: "Flowfield 9.9.1 is available" });
  await expect(notice).toBeVisible();
  await expect(
    notice.getByRole("link", { name: "Release notes" }),
  ).toHaveAttribute(
    "href",
    "https://github.com/flowfield-sh/flowfield-core/releases",
  );
  await expect(notice).toContainText("uv tool upgrade flowfield-core");
  await expect(notice).toContainText(
    "python -m pip install --upgrade flowfield-core",
  );
  await page.screenshot({
    path: "test-results/persistent-update-notification.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(notice).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(
    await notice.evaluate((card) => {
      const bounds = card.getBoundingClientRect();
      return [...card.querySelectorAll("p, pre")].every((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left >= bounds.left && rect.right <= bounds.right;
      });
    }),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/persistent-update-notification-mobile.png",
  });
  await notice
    .getByRole("button", { name: "Dismiss Flowfield 9.9.1 is available" })
    .click();
  await expect(notice).toHaveCount(0);
  await sheet
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  await expect.poll(() => manualChecks).toBe(1);
  await expect(notice).toHaveCount(0);
  await page.reload();
  await page
    .getByRole("button", { name: "Open projects", exact: true })
    .click();
  await page.getByRole("button", { name: /Notifications/ }).click();
  await expect(notice).toHaveCount(0);
  const secondContext = await browser.newContext();
  await stubModelCatalog(secondContext);
  const second = await secondContext.newPage();
  await second.goto("/");
  await second.getByRole("button", { name: /Notifications/ }).click();
  await expect(
    second.getByRole("heading", { name: "Flowfield 9.9.1 is available" }),
  ).toHaveCount(0);
  await sheet
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  await expect.poll(() => manualChecks).toBe(2);
  await expect(
    sheet.getByRole("heading", { name: "Flowfield 9.9.2 is available" }),
  ).toBeVisible();
  await expect(
    second.getByRole("heading", { name: "Flowfield 9.9.2 is available" }),
  ).toBeVisible();
  await sheet
    .getByRole("button", { name: "Clear notifications", exact: true })
    .click();
  await expect(
    second.getByRole("heading", { name: "Flowfield 9.9.2 is available" }),
  ).toHaveCount(0);
  await secondContext.close();
});

test("manual update checks announce completion without reporting an early success", async ({
  page,
  request,
}) => {
  const original = await (await request.get("/api/updates")).json();
  let checking = false;
  let completed = false;
  await page.route("**/api/updates", (route) =>
    route.fulfill({
      json: {
        ...original,
        checking: checking && !completed,
        error: null,
        latest_version: null,
        available_version: null,
      },
    }),
  );
  await page.route("**/api/updates/check", (route) => {
    if (route.request().postDataJSON().reason === "manual") checking = true;
    return route.fulfill({
      json: { ...original, checking, error: null, available_version: null },
    });
  });
  await page.goto("/");
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await page.getByRole("button", { name: /Notifications/ }).click();
  const sheet = page.getByRole("dialog", {
    name: "Notifications",
    exact: true,
  });
  await sheet
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  const info = page.locator("[data-sonner-toast][data-type=info]");
  await expect(info).toContainText("Checking for updates…");
  await expect(info).not.toContainText("No newer compatible release");
  completed = true;
  await expect(info).toContainText("No newer compatible release found.", {
    timeout: 10000,
  });
  await info.getByRole("button", { name: "Close toast" }).click();
  await expect(info).toHaveCount(0);
  await expect(sheet).toBeVisible();
});
