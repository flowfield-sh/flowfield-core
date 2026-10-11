import { join } from "node:path";
import { execFileSync } from "node:child_process";
import {
  expect,
  type Page,
  type BrowserContext,
  type APIRequestContext,
} from "@playwright/test";
import { test, existingDirectory, state, fixtureStages } from "./support";

test.beforeEach(async ({ request }) => {
  await request.patch("/api/announcements/settings", {
    data: { system_enabled: false, work_enabled: true, updates_enabled: false },
  });
});

async function setup(request: APIRequestContext, project: string) {
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix:
            project === "notify-act" ? "NAC" : project.slice(-3).toUpperCase(),
        },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post(`/api/projects/${project}/tasks`, {
        data: {
          stages: fixtureStages(),
          id: "task",
          title: "Notification work",
        },
      })
    ).ok(),
  ).toBe(true);
}
async function ask(request: APIRequestContext, project: string, id: string) {
  const response = await request.post(`/api/projects/${project}/questions`, {
    data: {
      id,
      task_id: "task",
      question: `Choose ${id}`,
      context: "A meaningful choice.",
      recommendation: "Keep it small.",
      blocking_scope: "Behavior",
    },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}
async function ready(page: Page, url: string) {
  const poll = page.waitForResponse("**/api/announcements/poll");
  await page.goto(url);
  await poll;
}
async function systemStub(
  context: BrowserContext,
  active = false,
  permission = "granted",
) {
  await context.addInitScript(
    ({ active, permission }) => {
      const target = window as unknown as {
        noticeActive: boolean;
        demoNotices: { title: string; onclick: () => void }[];
      };
      target.noticeActive = active;
      target.demoNotices = [];
      class TestNotification {
        static permission = permission;
        static async requestPermission() {
          return this.permission;
        }
        onclick = () => {};
        constructor(
          public title: string,
          public options: NotificationOptions,
        ) {
          target.demoNotices.push(this);
        }
        close() {}
      }
      Object.defineProperty(window, "Notification", {
        value: TestNotification,
      });
      Object.defineProperty(document, "visibilityState", {
        get: () => (target.noticeActive ? "visible" : "hidden"),
      });
      Object.defineProperty(document, "hasFocus", {
        value: () => target.noticeActive,
      });
    },
    { active, permission },
  );
}
const notices = (page: Page) =>
  page.evaluate(
    () => (window as unknown as { demoNotices: unknown[] }).demoNotices.length,
  );
const alertFor = (page: Page, text: string) =>
  page.locator("[data-sonner-toast]").filter({ hasText: text });

// Local operation feedback remains independent of background announcement preferences.
test("queue failures have contextual recovery and no notification center", async ({
  page,
  request,
}, testInfo) => {
  await setup(request, "notify-que");
  await ready(page, "/projects/notify-que");
  await page.getByRole("button", { name: "Run queue", exact: true }).click();
  await expect(alertFor(page, "Queue could not start")).toContainText(
    "Choose a worker harness and model",
  );
  await expect(
    page.getByRole("button", { name: /^Notifications/ }),
  ).toHaveCount(0);
  expect(
    (
      await request.post("/api/notifications/operations", { data: {} })
    ).status(),
  ).toBe(404);
  await page.getByRole("link", { name: "Choose model", exact: true }).click();
  await expect(page).toHaveURL(/edit\/workers$/);
  const workers = await (
    await request.get("/api/projects/notify-que/workers")
  ).json();
  await page.route("**/api/projects/notify-que/workers", (route) =>
    route.fulfill({
      json: {
        ...workers,
        problem: "The selected worker harness is unavailable.",
      },
    }),
  );
  await ready(page, "/projects/notify-que");
  await page.keyboard.press("Escape");
  const problem = page.getByTestId("work").getByRole("alert");
  await expect(problem).toContainText(
    "The selected worker harness is unavailable.",
  );
  await expect(
    problem.getByRole("link", { name: "Worker settings" }),
  ).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("queue-recovery-desktop.png"),
  });
});

test("active Inbox events toast once, preserve badges, navigate and do not replay", async ({
  page,
  request,
}, testInfo) => {
  await setup(request, "notify-act");
  await ready(page, "/projects/notify-act");
  await page.bringToFront();
  await expect.poll(() => page.evaluate(() => document.hasFocus())).toBe(true);
  await ask(request, "notify-act", "active-choice");
  const alert = alertFor(page, "Choose active-choice");
  await expect(alert).toBeVisible({ timeout: 10000 });
  await expect.poll(async () => (await alert.boundingBox())?.y).toBe(24);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("inbox-toast-desktop.png"),
  });
  const counts = await (
    await request.get("/api/projects/attention-counts")
  ).json();
  expect(counts["notify-act"]).toBe(1);
  await alert.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page).toHaveURL(
    /tasks\/NAC-1\/conversation\/question:active-choice:1/,
  );
  await expect(
    page.locator("[data-message-id='question:active-choice:1']"),
  ).toBeVisible();
  await page
    .getByLabel("Your answer", { exact: true })
    .fill("Use the small option");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/projects/attention-counts")).json())[
          "notify-act"
        ],
    )
    .toBe(0);
  await ready(page, "/projects/notify-act/inbox");
  await expect(alertFor(page, "Choose active-choice")).toHaveCount(0);
  expect(
    (await (await request.get("/api/projects/attention-counts")).json())[
      "notify-act"
    ],
  ).toBe(0);
});

test("background browser delivery is opt-in and exclusive across clients", async ({
  page,
  request,
  context,
}) => {
  await systemStub(context);
  await setup(request, "notify-bkg");
  await ready(page, "/settings/notifications");
  await page.getByLabel("Send system notifications when inactive").click();
  await expect(
    page.getByLabel("Send system notifications when inactive"),
  ).toBeChecked();
  const second = await context.newPage();
  await ready(second, "/projects/notify-bkg");
  await ask(request, "notify-bkg", "background-choice");
  await expect
    .poll(async () => (await notices(page)) + (await notices(second)), {
      timeout: 10000,
    })
    .toBe(1);
  const receiver = (await notices(page)) ? page : second;
  await receiver.evaluate(() =>
    (
      window as unknown as { demoNotices: { onclick(): void }[] }
    ).demoNotices[0].onclick(),
  );
  await expect(receiver).toHaveURL(
    /conversation\/question:background-choice:1/,
  );
  await receiver.reload();
  await receiver.waitForResponse("**/api/announcements/poll");
  expect(await notices(receiver)).toBe(0);
  await second.close();
});

test("active tab wins over a permitted background tab", async ({
  page,
  request,
  context,
}) => {
  await systemStub(context, false);
  await setup(request, "notify-win");
  await request.patch("/api/announcements/settings", {
    data: { system_enabled: true },
  });
  await ready(page, "/projects/notify-win");
  const active = await context.newPage();
  await ready(active, "/projects/notify-win");
  await active.evaluate(() => {
    (window as unknown as { noticeActive: boolean }).noticeActive = true;
    window.dispatchEvent(new Event("focus"));
  });
  await active.waitForResponse(
    (response) =>
      response.url().endsWith("/api/announcements/poll") &&
      response.request().postDataJSON().active === true,
  );
  await ask(request, "notify-win", "winner-choice");
  await expect(alertFor(active, "Choose winner-choice")).toBeVisible({
    timeout: 10000,
  });
  expect((await notices(active)) + (await notices(page))).toBe(0);
  await active.close();
});

test("visible exact question suppresses its announcement; work stays pending", async ({
  page,
  request,
  context,
}) => {
  await systemStub(context, true);
  await setup(request, "notify-vis");
  await ready(page, "/projects/notify-vis");
  await ask(request, "notify-vis", "visible-choice");
  await page.goto(
    "/projects/notify-vis/tasks/VIS-1/conversation/question:visible-choice:1",
  );
  await expect(
    page.locator(
      "[data-announcement-source='notify-vis:question:visible-choice']",
    ),
  ).toBeVisible();
  // Give the election a complete settled poll; do not infer suppression from a momentary absence.
  await page.waitForResponse("**/api/announcements/poll");
  await page.waitForResponse("**/api/announcements/poll");
  await expect(alertFor(page, "Choose visible-choice")).toHaveCount(0);
  expect(
    (await (await request.get("/api/projects/attention-counts")).json())[
      "notify-vis"
    ],
  ).toBe(1);
});

test("a burst groups into one Inbox toast without losing pending items", async ({
  page,
  request,
  context,
}) => {
  await systemStub(context, true);
  await setup(request, "notify-brs");
  await ready(page, "/projects/notify-brs");
  await Promise.all(
    Array.from({ length: 5 }, (_, n) =>
      ask(request, "notify-brs", `burst-${n}`),
    ),
  );
  const grouped = alertFor(page, "5 items need your attention");
  await expect(grouped).toBeVisible({ timeout: 10000 });
  await grouped.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page).toHaveURL(/notify-brs\/inbox$/);
  expect(
    (await (await request.get("/api/projects/attention-counts")).json())[
      "notify-brs"
    ],
  ).toBe(5);
});

test("denied permission is honest and test delivery is local and explicit", async ({
  page,
  context,
  request,
}) => {
  await systemStub(context, true, "denied");
  await ready(page, "/settings/notifications");
  await expect(
    page.getByText("Notifications are blocked.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send test notification" }),
  ).toBeDisabled();
  await page.getByLabel("Send system notifications when inactive").click();
  await expect(alertFor(page, "Could not update notifications")).toBeVisible();
  expect(
    (await (await request.get("/api/announcements/settings")).json())
      .system_enabled,
  ).toBe(false);
  await page.evaluate(() =>
    Object.defineProperty(Notification, "permission", { value: "granted" }),
  );
  await expect(
    page.getByRole("button", { name: "Send test notification" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Send test notification" }).click();
  expect(await notices(page)).toBe(1);
  expect(
    (await (await request.get("/api/announcements/settings")).json())
      .system_enabled,
  ).toBe(false);
});

test("alert navigation preserves an unsaved settings draft", async ({
  page,
  request,
  context,
}) => {
  await systemStub(context, true);
  await setup(request, "notify-drf");
  await ready(page, "/projects/notify-drf/edit/general");
  const name = page.getByRole("textbox", { name: "Name", exact: true });
  await name.fill("Keep this draft");
  await ask(request, "notify-drf", "draft-choice");
  const alert = alertFor(page, "Choose draft-choice");
  await expect(alert).toBeVisible({ timeout: 10000 });
  await expect.poll(async () => (await alert.boundingBox())?.y).toBe(24);
  await alert.getByRole("button", { name: "Open", exact: true }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(name).toHaveValue("Keep this draft");
});

const observe = (version: string) =>
  execFileSync("uv", [
    "run",
    "--project",
    "..",
    "../tests/notification_browser_fixture.py",
    state,
    version,
  ]);
test("updates live in Settings after their announcement closes, with mobile layout", async ({
  page,
  request,
  context,
}, testInfo) => {
  await systemStub(context, true);
  await request.patch("/api/announcements/settings", {
    data: { updates_enabled: true },
  });
  await ready(page, "/settings/notifications");
  observe("9.9.1");
  const announcement = alertFor(page, "Flowfield 9.9.1 is available");
  await expect(announcement).toBeVisible({ timeout: 10000 });
  await announcement.getByRole("button", { name: "Close toast" }).click();
  await expect(announcement).toHaveCount(0);
  await page.getByRole("tab", { name: "Updates", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "Updates", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("heading", { name: "Flowfield 9.9.1 is available" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Release notes", exact: true }),
  ).toHaveAttribute(
    "href",
    "https://github.com/flowfield-sh/flowfield-core/releases",
  );
  await expect(page.locator("pre")).toContainText([
    "uv tool upgrade",
    "python -m pip",
  ]);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("updates-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Flowfield 9.9.1 is available" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("updates-mobile.png"),
  });
  await page.reload();
  await page.waitForResponse("**/api/announcements/poll");
  await expect(announcement).toHaveCount(0);
});

test("manual checks wait for completion, survive navigation and report failure", async ({
  page,
  request,
  context,
}) => {
  await systemStub(context, true);
  const original = await (await request.get("/api/updates")).json();
  let checking = false,
    completed = false;
  await page.route("**/api/updates", (route) =>
    route.fulfill({
      json: {
        ...original,
        checking: checking && !completed,
        error: completed ? "Check failed; try again." : null,
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
  await ready(page, "/settings/updates");
  await page
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  await expect(alertFor(page, "Checking for updates…")).toBeVisible();
  await expect(alertFor(page, "No newer compatible release")).toHaveCount(0);
  await page.getByRole("tab", { name: "Notifications", exact: true }).click();
  completed = true;
  await expect(alertFor(page, "Check failed; try again.")).toBeVisible({
    timeout: 10000,
  });
});

test("unsupported system delivery keeps in-app preferences usable", async ({
  page,
  context,
}, testInfo) => {
  await context.addInitScript(() => {
    Reflect.deleteProperty(window, "Notification");
  });
  await ready(page, "/settings/notifications");
  await expect(
    page.getByText("System notifications are unavailable in this environment."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send test notification" }),
  ).toBeDisabled();
  await page.getByLabel("Announce new Inbox items").click();
  await expect(page.getByLabel("Announce new Inbox items")).not.toBeChecked();
  await page
    .locator("[data-sonner-toast]")
    .getByRole("button", { name: "Close toast" })
    .click();
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("notifications-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("notifications-mobile.png"),
  });
});

test("resolved permission links open the exact request without permitting anything", async ({
  page,
  request,
}) => {
  await setup(request, "notify-prm");
  const now = new Date().toISOString();
  await page.route(
    "**/api/projects/notify-prm/permissions/expired?*",
    (route) =>
      route.fulfill({
        json: {
          id: "expired",
          project_id: "notify-prm",
          role: "coordinator",
          title: "Read project files",
          details: "Read-only inspection",
          status: "expired",
          revision: 1,
          options: [],
          created_at: now,
          updated_at: now,
          expires_at: now,
          released_at: null,
          answer: null,
        },
      }),
  );
  await ready(page, "/projects/notify-prm/inbox?permission=expired");
  const dialog = page.getByRole("dialog", {
    name: "Tool permission",
    exact: true,
  });
  await expect(dialog).toContainText("Read project files");
  await expect(dialog).toContainText("Expired. No permission was delivered.");
  await expect(dialog.getByRole("button", { name: "Allow once" })).toHaveCount(
    0,
  );
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/notify-prm\/inbox$/);
});
