import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection } from "./DetailLayout";
import { Separator } from "@/components/ui/separator";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { StatusNotice } from "./StatusNotice";
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
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<Partial<AnnouncementSettings>>({});
  const acting = useRef(false);
  const mounted = useRef(true);
  const [, refreshPermission] = useState(0);
  const retry = settings.retry;
  useEffect(() => {
    if (settings.loading || busy) return;
    const timer = setTimeout(retry, 3000);
    return () => clearTimeout(timer);
  }, [retry, settings.loading, busy]);
  useEffect(() => {
    mounted.current = true;
    const refresh = () => refreshPermission((n) => n + 1);
    const timer = setInterval(refresh, 3000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      mounted.current = false;
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  const permission = host.notifications.permission();
  const environment = host.notifications.kind === "browser" ? "browser" : "app";
  const value = settings.data ? { ...settings.data, ...pending } : null;
  async function act(
    label: string,
    action: () => Promise<unknown>,
    change: Partial<AnnouncementSettings> = {},
  ) {
    if (acting.current) return;
    acting.current = true;
    settings.invalidate();
    setPending(change);
    setBusy(label);
    try {
      await action();
    } catch (error) {
      reportError(error, "Could not update notifications");
    } finally {
      acting.current = false;
      if (mounted.current) {
        setBusy(null);
        setPending({});
        refreshPermission((n) => n + 1);
        retry(); // Reconcile even an uncertain save; never retry the mutation itself.
      }
    }
  }
  async function save(change: Partial<AnnouncementSettings>) {
    if (!mounted.current) return;
    setBusy("Saving notification preferences…");
    const value = await request<AnnouncementSettings>(
      "announcements/settings",
      "PATCH",
      change,
    );
    if (mounted.current) {
      settings.invalidate();
      settings.setData(value);
    }
    toast.success("Notification preferences saved.");
  }
  async function allow() {
    const current = host.notifications.permission();
    const result =
      current === "default"
        ? await host.notifications.requestPermission()
        : current;
    if (result === "default") {
      toast.info(
        "Permission was not granted. Notification preferences are unchanged.",
      );
      return false;
    }
    if (result !== "granted")
      throw new Error(
        result === "denied"
          ? `Notifications are blocked. Allow them in your ${environment} or system settings.`
          : "System notifications are unavailable in this environment.",
      );
    return true;
  }
  function toggle(change: Partial<AnnouncementSettings>) {
    void act("Saving notification preferences…", () => save(change), change);
  }
  return (
    <ContentStack space="section">
      {settings.error && (
        <Button
          size="sm"
          variant="outline"
          disabled={settings.loading}
          onClick={settings.retry}
        >
          Retry notification settings
        </Button>
      )}
      {!value && !settings.error && (
        <StatusNotice
          title="Loading notification preferences…"
          tone="progress"
        />
      )}
      {value && (
        <fieldset
          disabled={!!busy}
          aria-busy={!!busy}
          className="content-stack"
          data-space="section"
        >
          <DetailSection title="Announcements">
            <p>
              Choose which events to announce across this workspace. While
              Flowfield is active, announcements appear as toasts.
            </p>
            <SettingsCheckbox
              checked={value.work_enabled}
              onChange={(e) => toggle({ work_enabled: e.target.checked })}
            >
              Announce new Inbox items
            </SettingsCheckbox>
            <SettingsCheckbox
              checked={value.updates_enabled}
              onChange={(e) => toggle({ updates_enabled: e.target.checked })}
            >
              Announce available updates
            </SettingsCheckbox>
          </DetailSection>
          <Separator />
          <DetailSection title="When inactive">
            <SettingsCheckbox
              hint={`Delivery requires permission on each ${environment}. Flowfield must remain running.`}
              checked={value.system_enabled}
              disabled={
                !value.system_enabled &&
                (permission === "denied" || permission === "unavailable")
              }
              onChange={(e) => {
                const enabled = e.target.checked;
                const requestedFrom = window.location.href;
                void act(
                  enabled && permission === "default"
                    ? "Waiting for notification permission…"
                    : "Saving notification preferences…",
                  async () => {
                    if (enabled && !(await allow())) return;
                    // The router changes the URL before its transition unmounts us.
                    if (window.location.href !== requestedFrom) return;
                    await save({ system_enabled: enabled });
                  },
                  { system_enabled: enabled },
                );
              }}
            >
              Send system notifications when inactive
            </SettingsCheckbox>
            <StatusNotice
              tone={
                busy
                  ? "progress"
                  : permission === "denied" ||
                      permission === "unavailable" ||
                      (value.system_enabled && permission !== "granted")
                    ? "attention"
                    : permission === "granted" && value.system_enabled
                      ? "success"
                      : "neutral"
              }
              title={
                busy ??
                (permission === "granted"
                  ? value.system_enabled
                    ? "System notifications enabled"
                    : "System notifications off"
                  : permission === "denied"
                    ? "Notifications blocked"
                    : permission === "unavailable"
                      ? "System notifications unavailable"
                      : "Permission needed")
              }
              actions={
                <>
                  {permission === "default" && (
                    <Button
                      variant="outline"
                      onClick={() =>
                        void act(
                          "Waiting for notification permission…",
                          async () => {
                            if (await allow())
                              toast.success("Notification permission granted.");
                          },
                        )
                      }
                    >
                      Allow on this {environment}
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    disabled={permission !== "granted"}
                    onClick={() =>
                      void act("Sending test notification…", async () => {
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
                </>
              }
            >
              {!busy && (
                <p>
                  {permission === "granted"
                    ? value.system_enabled
                      ? `This ${environment} can notify you while Flowfield is inactive.`
                      : `This ${environment} has permission. Turn on inactive delivery to receive alerts.`
                    : permission === "denied"
                      ? "Allow notifications in your browser or system settings, then return here."
                      : permission === "unavailable"
                        ? "In-app announcements remain available."
                        : `Allow notifications on this ${environment} to use system delivery.`}
                </p>
              )}
              {!busy && value.system_enabled && permission !== "granted" && (
                <p>
                  System notifications are enabled for the workspace, but this{" "}
                  {environment}{" "}
                  {permission === "unavailable"
                    ? "does not support delivery."
                    : "cannot deliver them until permission is granted."}
                </p>
              )}
            </StatusNotice>
          </DetailSection>
        </fieldset>
      )}
    </ContentStack>
  );
}
