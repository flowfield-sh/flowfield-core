import {
  fixtureProject,
  projectWorkspace,
  test,
  existingDirectory,
  state,
} from "./support";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import {
  expect,
  type Page,
  type BrowserContext,
  type APIRequestContext,
} from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.patch("/api/announcements/settings", {
    data: { system_enabled: false, work_enabled: true, updates_enabled: false },
  });
});

async function setup(request: APIRequestContext, project: string) {
  expect(
    (
      await fixtureProject(request, {
        data: {
          path: existingDirectory(join(state, project)),
        },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post(
        `/api/workspaces/${await projectWorkspace(request, project)}/tasks`,
        {
          data: {
            id: "task",
            title: "Notification work",
          },
        },
      )
    ).ok(),
  ).toBe(true);
}
async function ask(request: APIRequestContext, project: string, id: string) {
  const response = await request.post(
    `/api/workspaces/${await projectWorkspace(request, project)}/questions`,
    {
      data: {
        id,
        task_id: "task",
        question: `Choose ${id}`,
        context: "A meaningful choice.",
        recommendation: "Keep it small.",
        blocking_scope: "Behavior",
      },
    },
  );
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
  const workspace = await projectWorkspace(request, "notify-que");
  await page.route(`**/api/workspaces/${workspace}/queue`, (route) =>
    route.fulfill({
      status: 409,
      json: {
        error: {
          code: "revision_conflict",
          message: "Queue settings changed. Reload and try again.",
        },
      },
    }),
  );
  await ready(page, "/projects/notify-que");
  await page.getByRole("button", { name: "Run queue", exact: true }).click();
  await expect(alertFor(page, "Queue could not start")).toContainText(
    "Queue settings changed. Reload and try again.",
  );
  await expect(
    page.getByRole("button", { name: /^Notifications/ }),
  ).toHaveCount(0);
  expect(
    (
      await request.post("/api/notifications/operations", { data: {} })
    ).status(),
  ).toBe(404);
  await page
    .getByRole("link", { name: "Worker settings", exact: true })
    .click();
  await expect(page).toHaveURL(/edit\/workers$/);
  const workers = await (
    await request.get(`/api/workspaces/${workspace}/workers`)
  ).json();
  await page.route(`**/api/workspaces/${workspace}/workers`, (route) =>
    route.fulfill({
      json: {
        ...workers,
        problem: "The selected worker harness is unavailable.",
      },
    }),
  );
  await ready(page, "/projects/notify-que");
  await page.keyboard.press("Escape");
  const problem = page.locator(".queue-controls").getByRole("alert");
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
    await request.get("/api/workspaces/attention-counts")
  ).json();
  expect(counts[await projectWorkspace(request, "notify-act")]).toBe(1);
  await alert.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page).toHaveURL(
    /tasks\/T-1\/conversation\/question:active-choice:1/,
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
        (await (await request.get("/api/workspaces/attention-counts")).json())[
          await projectWorkspace(request, "notify-act")
        ],
    )
    .toBe(0);
  await ready(
    page,
    `/workspaces/${await projectWorkspace(request, "notify-act")}/inbox`,
  );
  await expect(alertFor(page, "Choose active-choice")).toHaveCount(0);
  expect(
    (await (await request.get("/api/workspaces/attention-counts")).json())[
      await projectWorkspace(request, "notify-act")
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
  const workspace = await projectWorkspace(request, "notify-vis");
  await page.goto(
    `/workspaces/${workspace}/tasks/T-1/conversation/question:visible-choice:1`,
  );
  await expect(
    page.locator(
      `[data-announcement-source='${workspace}:question:visible-choice']`,
    ),
  ).toBeVisible();
  // Give the election a complete settled poll; do not infer suppression from a momentary absence.
  await page.waitForResponse("**/api/announcements/poll");
  await page.waitForResponse("**/api/announcements/poll");
  await expect(alertFor(page, "Choose visible-choice")).toHaveCount(0);
  expect(
    (await (await request.get("/api/workspaces/attention-counts")).json())[
      await projectWorkspace(request, "notify-vis")
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
  await expect(page).toHaveURL(
    `/workspaces/${await projectWorkspace(request, "notify-brs")}/inbox`,
  );
  expect(
    (await (await request.get("/api/workspaces/attention-counts")).json())[
      await projectWorkspace(request, "notify-brs")
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
    page.getByRole("heading", { name: "Notifications blocked", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send test notification" }),
  ).toBeDisabled();
  await expect(
    page.getByLabel("Send system notifications when inactive"),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Allow on this browser" }),
  ).toHaveCount(0);
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
  request,
}, testInfo) => {
  await context.addInitScript(() => {
    Reflect.deleteProperty(window, "Notification");
  });
  await ready(page, "/settings/notifications");
  await expect(
    page.getByRole("heading", {
      name: "System notifications unavailable",
      exact: true,
    }),
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
  await request.patch("/api/announcements/settings", {
    data: { system_enabled: true },
  });
  const checkbox = page.getByLabel("Send system notifications when inactive");
  await expect(checkbox).toBeChecked();
  await expect(checkbox).toBeEnabled();
  await expect(
    page.getByText(
      /enabled for the workspace, but this browser does not support delivery/,
    ),
  ).toBeVisible();
  await checkbox.click();
  await expect.poll(() => systemEnabled(request)).toBe(false);
  await expect(checkbox).toBeDisabled();
});

test("resolved permission links open the exact request without permitting anything", async ({
  page,
  request,
}) => {
  await setup(request, "notify-prm");
  const workspace = await projectWorkspace(request, "notify-prm");
  const now = new Date().toISOString();
  await page.route(
    `**/api/workspaces/${workspace}/permissions/expired?*`,
    (route) =>
      route.fulfill({
        json: {
          id: "expired",
          project_id: null,
          workspace_id: workspace,
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
  await ready(page, `/workspaces/${workspace}/inbox?permission=expired`);
  const dialog = page.getByRole("region", {
    name: "Tool permission",
    exact: true,
  });
  await expect(dialog).toContainText("Read project files");
  await expect(dialog).toContainText("Expired. No permission was delivered.");
  await expect(dialog.getByRole("button", { name: "Allow once" })).toHaveCount(
    0,
  );
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(`/workspaces/${workspace}/inbox`);
});

async function pendingPermission(page: Page) {
  await page.evaluate(() => {
    const target = window as unknown as {
      permissionRequests: number;
      resolvePermission(value: NotificationPermission): void;
    };
    target.permissionRequests = 0;
    Notification.requestPermission = () => {
      target.permissionRequests++;
      return new Promise((resolve) => {
        target.resolvePermission = (value) => {
          Object.defineProperty(Notification, "permission", {
            value,
            configurable: true,
          });
          resolve(value);
        };
      });
    };
  });
}
async function answerPermission(page: Page, value: NotificationPermission) {
  await page.evaluate((value) => {
    (
      window as unknown as {
        resolvePermission(value: NotificationPermission): void;
      }
    ).resolvePermission(value);
  }, value);
}
async function systemEnabled(request: APIRequestContext) {
  return (await (await request.get("/api/announcements/settings")).json())
    .system_enabled;
}
for (const permission of ["granted", "denied", "default"] as const) {
  test(`checkbox-first permission ${permission} has a visible pending state and accurate saved value`, async ({
    page,
    context,
    request,
  }, testInfo) => {
    await systemStub(context, true, "default");
    await ready(page, "/settings/notifications");
    await pendingPermission(page);
    const checkbox = page.getByLabel("Send system notifications when inactive");
    await checkbox.click();
    await expect(checkbox).toBeChecked();
    await expect(checkbox).toBeDisabled();
    await expect(
      page.getByText("Waiting for notification permission…"),
    ).toBeVisible();
    await expect(page.getByLabel("Announce new Inbox items")).toBeDisabled();
    expect(await systemEnabled(request)).toBe(false);
    if (permission === "granted")
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath("permission-pending.png"),
      });
    await answerPermission(page, permission);
    await expect
      .poll(() => systemEnabled(request))
      .toBe(permission === "granted");
    await expect(checkbox).toBeChecked({ checked: permission === "granted" });
    if (permission === "granted") {
      await expect(
        page.getByRole("heading", {
          name: "System notifications enabled",
          exact: true,
        }),
      ).toBeVisible();
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath("permission-enabled.png"),
      });
    } else if (permission === "denied") {
      await expect(checkbox).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "Allow on this browser" }),
      ).toHaveCount(0);
    } else {
      await expect(alertFor(page, "Permission was not granted.")).toBeVisible();
      await checkbox.click();
      await answerPermission(page, "granted");
      await expect.poll(() => systemEnabled(request)).toBe(true);
    }
  });
}

test("permission-first then repeated checkbox toggles requests permission only once", async ({
  page,
  context,
  request,
}) => {
  await systemStub(context, true, "default");
  await ready(page, "/settings/notifications");
  await pendingPermission(page);
  await page.getByRole("button", { name: "Allow on this browser" }).click();
  await answerPermission(page, "granted");
  await expect(
    page.getByRole("heading", {
      name: "System notifications off",
      exact: true,
    }),
  ).toBeVisible();
  expect(await systemEnabled(request)).toBe(false);
  const checkbox = page.getByLabel("Send system notifications when inactive");
  for (const enabled of [true, false, true, false]) {
    await expect(checkbox).toBeEnabled();
    await checkbox.click();
    await expect.poll(() => systemEnabled(request)).toBe(enabled);
    await expect(checkbox).toBeChecked({ checked: enabled });
  }
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { permissionRequests: number })
          .permissionRequests,
    ),
  ).toBe(1);
});

test("workspace preference and revoked local permission remain distinct across tabs", async ({
  page,
  context,
  request,
}) => {
  await systemStub(context, true, "default");
  await request.patch("/api/announcements/settings", {
    data: { system_enabled: true },
  });
  await ready(page, "/settings/notifications");
  const second = await context.newPage();
  await ready(second, "/settings/notifications");
  await expect(
    page.getByLabel("Send system notifications when inactive"),
  ).toBeChecked();
  await expect(
    page.getByText(/enabled for the workspace, but this browser cannot/),
  ).toBeVisible();
  await pendingPermission(page);
  await page.getByRole("button", { name: "Allow on this browser" }).click();
  await answerPermission(page, "granted");
  await expect(
    page.getByRole("heading", {
      name: "System notifications enabled",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    second.getByText(/enabled for the workspace, but this browser cannot/),
  ).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(Notification, "permission", { value: "denied" });
    window.dispatchEvent(new Event("focus"));
  });
  const checkbox = page.getByLabel("Send system notifications when inactive");
  await expect(
    page.getByRole("heading", { name: "Notifications blocked", exact: true }),
  ).toBeVisible();
  await expect(checkbox).toBeChecked();
  await expect(checkbox).toBeEnabled();
  await checkbox.click();
  await expect.poll(() => systemEnabled(request)).toBe(false);
  await expect(
    second.getByLabel("Send system notifications when inactive"),
  ).not.toBeChecked();
  await expect(checkbox).toBeDisabled();
  await second.close();
});

test("a failed save after granting permission rolls back and can be retried without another prompt", async ({
  page,
  context,
  request,
}) => {
  await systemStub(context, true, "default");
  await ready(page, "/settings/notifications");
  await pendingPermission(page);
  await page.route("**/api/announcements/settings", (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({ status: 503, json: {} })
      : route.continue(),
  );
  const checkbox = page.getByLabel("Send system notifications when inactive");
  await checkbox.click();
  await answerPermission(page, "granted");
  await expect(alertFor(page, "Could not update notifications")).toBeVisible();
  await expect(checkbox).not.toBeChecked();
  expect(await systemEnabled(request)).toBe(false);
  await expect(
    page.getByRole("heading", {
      name: "System notifications off",
      exact: true,
    }),
  ).toBeVisible();
  await page.unroute("**/api/announcements/settings");
  await checkbox.click();
  await expect.poll(() => systemEnabled(request)).toBe(true);
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { permissionRequests: number })
          .permissionRequests,
    ),
  ).toBe(1);
});

test("slow settings reads finish and permission decisions after navigation do not enable delivery", async ({
  page,
  context,
  request,
}) => {
  await systemStub(context, true, "default");
  await page.route("**/api/announcements/settings", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 3500));
    await route.fulfill({ response });
  });
  await ready(page, "/settings/notifications");
  const checkbox = page.getByLabel("Send system notifications when inactive");
  await expect(checkbox).toBeVisible({ timeout: 6000 });
  await pendingPermission(page);
  await checkbox.click();
  await page.getByRole("tab", { name: "Updates", exact: true }).click();
  // Resolve immediately after the URL changes, before a deferred React render.
  await expect(page).toHaveURL(/\/settings\/updates$/);
  await answerPermission(page, "granted");
  expect(await systemEnabled(request)).toBe(false);
  await page.unroute("**/api/announcements/settings");
});

test("a manual update result suppresses an automatic announcement already in flight", async ({
  page,
  context,
  request,
}) => {
  await systemStub(context, true);
  const original = await (await request.get("/api/updates")).json();
  const status = {
    ...original,
    available_version: "9.9.2",
    checking: false,
    error: null,
  };
  await page.route("**/api/updates", (route) =>
    route.fulfill({ json: status }),
  );
  await page.route("**/api/updates/check", (route) =>
    route.fulfill({ json: status }),
  );
  await ready(page, "/settings/notifications");
  let release: (() => void) | undefined;
  await page.route("**/api/announcements/poll", async (route) => {
    if (release) return route.continue();
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await route.fulfill({
      json: {
        channel: "toast",
        system_allowed: false,
        items: [
          {
            id: 9999,
            source_key: "update:9.9.2",
            title: "Automatic update announcement",
            message: "View update",
            href: "/settings/updates",
            project_id: null,
            kind: "update",
          },
        ],
      },
    });
  });
  await expect.poll(() => !!release).toBe(true);
  await page.getByRole("tab", { name: "Updates", exact: true }).click();
  await page
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  await expect(alertFor(page, "Flowfield 9.9.2 is available.")).toBeVisible();
  await page.getByRole("tab", { name: "Notifications", exact: true }).click();
  const response = page.waitForResponse("**/api/announcements/poll");
  release!();
  await response;
  await page.unroute("**/api/announcements/poll");
  await page.waitForResponse("**/api/announcements/poll");
  await expect(alertFor(page, "Automatic update announcement")).toHaveCount(0);
});

for (const scenario of [
  {
    name: "unchecked",
    title: "Ready to check for updates",
    last_success: null,
    checking: false,
    error: null,
  },
  {
    name: "current",
    title: "Flowfield is up to date",
    last_success: "2026-10-11T12:00:00Z",
    checking: false,
    error: null,
  },
  {
    name: "checking",
    title: "Checking for updates…",
    last_success: null,
    checking: true,
    error: null,
  },
  {
    name: "failed",
    title: "Update check needs attention",
    last_success: null,
    checking: false,
    error: "The release service is unavailable. Try again later.",
  },
]) {
  test(`update settings identify ${scenario.name} status and its action`, async ({
    page,
    request,
  }, testInfo) => {
    const original = await (await request.get("/api/updates")).json();
    await page.route("**/api/updates", (route) =>
      route.fulfill({
        json: {
          ...original,
          ...scenario,
          available_version: null,
        },
      }),
    );
    await page.goto("/settings/updates");
    const panel = page.getByRole("tabpanel", { name: "Updates", exact: true });
    await expect(
      panel.getByRole("heading", { name: "Updates", exact: true }),
    ).toHaveCount(0);
    const status = panel.getByRole("status");
    await expect(
      panel.locator('input[type="checkbox"], [role="status"]').first(),
    ).toHaveAccessibleName("Check for updates automatically");
    await expect(
      status.getByRole("heading", { name: scenario.title, exact: true }),
    ).toBeVisible();
    const check = status.getByRole("button", {
      name: scenario.checking ? "Checking…" : "Check for updates",
      exact: true,
    });
    if (scenario.checking) await expect(check).toBeDisabled();
    else await expect(check).toBeEnabled();
    await expect(
      panel.getByRole("checkbox", { name: "Check for updates automatically" }),
    ).toHaveAccessibleDescription(
      "Automatic checks are disabled by the service configuration.",
    );
    if (process.env.FLOWFIELD_VISUAL_EVIDENCE) {
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath(`updates-${scenario.name}-desktop.png`),
      });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
      await page.reload();
      await expect(
        status.getByRole("heading", { name: scenario.title, exact: true }),
      ).toBeVisible();
      await page.screenshot({
        animations: "disabled",
        path: testInfo.outputPath(`updates-${scenario.name}-mobile-dark.png`),
      });
    }
  });
}

test("notification settings group delivery permission and controls without repeating the tab title", async ({
  page,
  context,
}, testInfo) => {
  await systemStub(context, true, "default");
  await page.goto("/settings/notifications");
  const panel = page.getByRole("tabpanel", {
    name: "Notifications",
    exact: true,
  });
  await expect(
    panel.getByRole("heading", { name: "Notifications", exact: true }),
  ).toHaveCount(0);
  await expect(
    panel.getByRole("heading", { name: "Announcements", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("heading", { name: "When inactive", exact: true }),
  ).toBeVisible();
  const delivery = panel.getByRole("checkbox", {
    name: "Send system notifications when inactive",
  });
  await expect(delivery).toHaveAccessibleDescription(
    "Delivery requires permission on each browser. Flowfield must remain running.",
  );
  const status = panel.getByRole("status");
  await expect(
    status.getByRole("heading", { name: "Permission needed" }),
  ).toBeVisible();
  await expect(
    status.getByRole("button", { name: "Allow on this browser" }),
  ).toBeEnabled();
  await expect(
    status.getByRole("button", { name: "Send test notification" }),
  ).toBeDisabled();
  if (process.env.FLOWFIELD_VISUAL_EVIDENCE) {
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath("notifications-default-desktop.png"),
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.reload();
    await expect(
      status.getByRole("heading", { name: "Permission needed" }),
    ).toBeVisible();
    await page.screenshot({
      animations: "disabled",
      path: testInfo.outputPath("notifications-default-mobile-dark.png"),
    });
  }
});
