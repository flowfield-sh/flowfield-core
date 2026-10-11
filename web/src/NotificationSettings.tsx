import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { useHost } from "./HostCapabilities";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";
import type { AnnouncementSettings } from "./Announcements";

export function NotificationSettings() {
  const host = useHost();
  const settings = useResource<AnnouncementSettings>(
    "announcements/settings",
    0,
  );
  const [busy, setBusy] = useState(false);
  const [, refreshPermission] = useState(0);
  const retry = settings.retry;
  useEffect(() => {
    const timer = setInterval(() => {
      retry();
      refreshPermission((n) => n + 1);
    }, 3000);
    return () => clearInterval(timer);
  }, [retry]);
  const permission = host.notifications.permission();
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      reportError(error, "Could not update notifications");
    } finally {
      setBusy(false);
      refreshPermission((n) => n + 1);
    }
  }
  async function save(change: Partial<AnnouncementSettings>) {
    if (!settings.data) return;
    const value = await request<AnnouncementSettings>(
      "announcements/settings",
      "PATCH",
      change,
    );
    settings.invalidate();
    settings.setData(value);
    toast.success("Notification preferences saved.");
  }
  return (
    <ContentStack space="section">
      <DetailHeading title="Notifications" />
      <p>
        New Inbox items appear as a toast while Flowfield is active. When you
        are elsewhere, Flowfield can send a system notification.
      </p>
      <p className="detail-metadata">
        Preferences apply across this workspace. Permission applies to this{" "}
        {host.notifications.kind === "browser" ? "browser" : "app"}.
      </p>
      {settings.error && (
        <Button variant="outline" onClick={settings.retry}>
          Retry notification settings
        </Button>
      )}
      {settings.data && (
        <fieldset disabled={busy} className="content-stack">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={settings.data.work_enabled}
              onChange={(e) =>
                void act(() => save({ work_enabled: e.target.checked }))
              }
            />
            Announce new Inbox items
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={settings.data.updates_enabled}
              onChange={(e) =>
                void act(() => save({ updates_enabled: e.target.checked }))
              }
            />
            Announce available updates
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={settings.data.system_enabled}
              onChange={(e) => {
                const enabled = e.target.checked;
                void act(async () => {
                  if (
                    enabled &&
                    (await host.notifications.requestPermission()) !== "granted"
                  )
                    throw new Error(
                      "Notifications are not allowed. Change this browser or app's notification permission to enable delivery here.",
                    );
                  await save({ system_enabled: enabled });
                });
              }}
            />
            Send system notifications when inactive
          </label>
          <p role="status">
            {permission === "granted"
              ? "Permission granted."
              : permission === "denied"
                ? "Notifications are blocked. Allow them in your browser or system settings."
                : permission === "unavailable"
                  ? "System notifications are unavailable in this environment."
                  : "Notification permission has not been granted."}
          </p>
          <div className="actions">
            {permission !== "granted" && permission !== "unavailable" && (
              <Button
                variant="outline"
                onClick={() =>
                  void act(async () => {
                    const result = await host.notifications.requestPermission();
                    if (result !== "granted")
                      throw new Error(
                        "Allow notifications in your browser or system settings.",
                      );
                  })
                }
              >
                Allow on this{" "}
                {host.notifications.kind === "browser" ? "browser" : "app"}
              </Button>
            )}
            <Button
              variant="outline"
              disabled={permission !== "granted"}
              onClick={() =>
                void act(async () => {
                  await host.notifications.present(
                    {
                      title: "Flowfield notifications work",
                      message:
                        "New alerts can appear here when Flowfield is inactive.",
                      href: "/settings/notifications",
                      source_key: `test:${crypto.randomUUID()}`,
                    },
                    () => window.focus(),
                  );
                  toast.success("Test notification sent.");
                })
              }
            >
              Send test notification
            </Button>
          </div>
        </fieldset>
      )}
    </ContentStack>
  );
}
