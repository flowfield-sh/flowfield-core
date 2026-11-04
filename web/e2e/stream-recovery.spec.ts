import { expect } from "@playwright/test";
import { test, fixtureProject, existingDirectory, state } from "./support";
import { createServer, request as proxyRequest } from "node:http";
import type { AddressInfo } from "node:net";
import { join } from "node:path";

test("a permanently closed event stream reconnects without losing drafts", async ({
  page,
  request,
  browserName,
  service,
}) => {
  const id = `closed-stream-${browserName}`;
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  let attempts = 0;
  let recover = false;
  // Intercept at HTTP level so this covers the real SharedWorker transport too.
  const upstream = new URL(service.baseURL);
  const proxy = createServer((incoming, outgoing) => {
    if (incoming.url === "/api/events") {
      attempts++;
      if (!recover) {
        outgoing.writeHead(200, { "content-type": "text/plain" });
        outgoing.end("invalid stream response");
        return;
      }
    }
    const relay = proxyRequest(
      {
        hostname: upstream.hostname,
        port: upstream.port,
        path: incoming.url,
        method: incoming.method,
        headers: incoming.headers,
      },
      (response) => {
        outgoing.writeHead(response.statusCode!, response.headers);
        response.pipe(outgoing);
      },
    );
    relay.on("error", () => outgoing.destroy());
    outgoing.on("close", () => relay.destroy());
    incoming.pipe(relay);
  });
  await new Promise<void>((resolve) => proxy.listen(0, "127.0.0.1", resolve));
  try {
    await page.goto(
      `http://127.0.0.1:${(proxy.address() as AddressInfo).port}/projects/${id}/edit/general`,
    );
    const name = page
      .getByRole("region", { name: "Edit project", exact: true })
      .getByLabel("Name", { exact: true });
    await name.fill("Keep this unsaved draft");
    await expect(
      page.getByRole("status", { name: "Reconnecting…", exact: true }),
    ).toBeVisible();
    await expect.poll(() => attempts).toBeGreaterThan(1);
    recover = true;
    await expect(
      page.getByRole("status", { name: "Connected", exact: true }),
    ).toBeVisible({ timeout: 20000 });
    await expect(name).toHaveValue("Keep this unsaved draft");
    await expect(
      page.getByRole("dialog", { name: "Connection lost" }),
    ).toHaveCount(0);
  } finally {
    await page.close();
    proxy.closeAllConnections();
    await new Promise<void>((resolve) => proxy.close(() => resolve()));
  }
});

test("eight browser tabs stay responsive and receive updates after the first closes", async ({
  page,
  context,
  request,
}) => {
  const id = "shared-live-tabs";
  await fixtureProject(request, {
    data: { path: existingDirectory(join(state, id)) },
  });
  const { projectWorkspace } = await import("./support");
  const workspace = await projectWorkspace(request, id);
  const tabs = [page];
  for (let index = 0; index < 8; index++) {
    const tab = index === 0 ? page : await context.newPage();
    if (index) tabs.push(tab);
    await tab.goto(`/workspaces/${workspace}/tasks`);
    await expect(
      tab.getByRole("status", { name: "Connected", exact: true }),
    ).toBeVisible();
    await expect(
      tab.getByRole("region", { name: "Backlog tasks", exact: true }),
    ).toBeVisible();
  }
  await tabs.shift()!.close();
  const response = await request.post(`/api/workspaces/${workspace}/tasks`, {
    data: { title: "Live across browser tabs" },
  });
  expect(response.ok()).toBe(true);
  for (const tab of tabs) {
    await expect(
      tab.getByRole("link", { name: /Live across browser tabs/ }),
    ).toBeVisible();
  }
  // One tab going offline/rejoining must not close the others' shared connection.
  await tabs[0].evaluate(() => window.dispatchEvent(new Event("offline")));
  expect(
    (
      await request.post(`/api/workspaces/${workspace}/tasks`, {
        data: { title: "Created while this tab was offline" },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    tabs
      .at(-1)!
      .getByRole("link", { name: /Created while this tab was offline/ }),
  ).toBeVisible();
  await tabs[0].evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(
    tabs[0].getByRole("status", { name: "Connected", exact: true }),
  ).toBeVisible();
  await expect(
    tabs[0].getByRole("link", { name: /Created while this tab was offline/ }),
  ).toBeVisible();
  for (const tab of tabs) await tab.close();
});
