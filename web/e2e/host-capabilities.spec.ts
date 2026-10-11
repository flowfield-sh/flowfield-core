import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "@playwright/test";
import {
  HostProvider,
  useHost,
  browserHost,
  type HostCapabilities,
  type UpdateStatus,
} from "../src/HostCapabilities";
import { groupAnnouncements } from "../src/Announcements";

// These contract fakes establish the host seam; they do not prove native OS behavior.
test("host composition supports native updates without enabling package installation", async () => {
  let installed = false;
  const status: UpdateStatus = {
    installed_version: "1",
    available_version: "2",
    treatment: "app",
    automatic: true,
    disabled_by_environment: false,
    checking: false,
    cached: false,
    release_notes: "https://example.com/releases",
  };
  const native: HostCapabilities = {
    notifications: {
      kind: "native",
      permission: () => "unavailable",
      requestPermission: async () => "unavailable",
      present: async () => {
        throw new Error("Unavailable");
      },
    },
    updates: {
      status: async () => status,
      check: async () => status,
      configure: async () => status,
      actions: (value) =>
        value.available_version
          ? [
              {
                id: "restart",
                label: "Restart to update",
                run: async () => {
                  installed = true;
                },
              },
            ]
          : [],
    },
  };
  function View() {
    const host = useHost();
    return createElement(
      "div",
      null,
      host.notifications.permission(),
      ...host.updates.actions(status).map((action) => action.label),
    );
  }
  expect(
    renderToStaticMarkup(
      createElement(HostProvider, {
        host: native,
        children: createElement(View),
      }),
    ),
  ).toContain("Restart to update");
  expect(browserHost.updates.actions(status)).toEqual([]);
  await native.updates.actions(await native.updates.status())[0].run();
  expect(installed).toBe(true);
  await expect(
    native.notifications.present(
      { title: "Test", message: "Test", href: "/", source_key: "test" },
      () => {},
    ),
  ).rejects.toThrow("Unavailable");
});

test("large bursts retain a destination across projects and preserve update announcements", () => {
  const items = Array.from({ length: 200 }, (_, id) => ({
    id,
    source_key: `project:question:${id}`,
    project_id: id % 2 ? "first" : "last",
    kind: "work" as const,
    title: "Question",
    message: "Needs an answer",
    href: "/",
  }));
  const update = {
    ...items[0],
    id: 201,
    kind: "update" as const,
    title: "Update",
    href: "/settings/updates",
  };
  const grouped = groupAnnouncements([...items, update]);
  expect(grouped).toHaveLength(2);
  expect(grouped[0].title).toBe("200 items need your attention");
  expect(grouped[0].href).toBe("/");
  expect(grouped[1]).toEqual(update);
});
