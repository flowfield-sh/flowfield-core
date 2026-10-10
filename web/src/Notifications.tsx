import { toast } from "sonner";
import { CountBadge } from "./CountBadge";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { WorkspaceLink } from "./WorkspaceLink";
import { browserNoticesSupported } from "./BrowserNotices";
import { request } from "./workspace";
import type { components } from "./api-schema";

type Notice = components["schemas"]["OperationNotice"];
type Page = components["schemas"]["NotificationPage"];
type Settings = components["schemas"]["NotificationSettings"];
type Update = components["schemas"]["UpdateStatus"];
function reportUpdate(result: Update) {
  const options = { id: "update-check" };
  if (result.error) toast.error(result.error, options);
  else if (result.available_version)
    toast.info(`Flowfield ${result.available_version} is available.`, options);
  else toast.info("No newer compatible release found.", options);
}
const Context = createContext<{
  notify: (notice: Notice, open?: boolean) => void;
  show: () => void;
  count: number;
} | null>(null);
export function useNotifications() {
  const value = useContext(Context);
  if (!value) throw new Error("Notifications provider missing");
  return value;
}

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<Page>({ items: [], through: 0 });
  const [settings, setSettings] = useState<Settings | null>(null);
  const [updates, setUpdates] = useState<Update | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [unsavedNotice, setUnsavedNotice] = useState<Notice | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const generation = useRef(0);
  const manualCheck = useRef(false);
  const show = useCallback(() => {
    if (document.activeElement instanceof HTMLElement)
      opener.current = document.activeElement;
    setOpen(true);
  }, []);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const current = ++generation.current;
    const [notices, preferences, update] = await Promise.all([
      request<Page>("notifications", "GET", undefined, signal),
      request<Settings>("notifications/settings", "GET", undefined, signal),
      request<Update>("updates", "GET", undefined, signal),
    ]);
    if (current !== generation.current || signal?.aborted) return;
    setPage(notices);
    setSettings(preferences);
    setUpdates(update);
    setLoadError("");
    if (manualCheck.current && !update.checking) {
      manualCheck.current = false;
      reportUpdate(update);
    }
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        await refresh(controller.signal);
        if (!stopped) setLoadError("");
      } catch (e) {
        if (!stopped) setLoadError((e as Error).message);
      }
      if (!stopped) timer = setTimeout(() => void poll(), 3000);
    }
    void request<Update>("updates/check", "POST", { reason: "startup" }).catch(
      () => {},
    );
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh]);
  const notify = useCallback(
    (notice: Notice, reveal = true) => {
      if (reveal) {
        toast.error(notice.title, {
          id: notice.key,
          description: notice.message,
          duration: 8000,
          action: notice.href ? (
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink
                to={notice.href}
                onClick={() => toast.dismiss(notice.key)}
              >
                {notice.action || "View details"}
              </WorkspaceLink>
            </Button>
          ) : undefined,
        });
      }
      const occurrence =
        reveal && !notice.occurrence
          ? { ...notice, occurrence: crypto.randomUUID() }
          : notice;
      ++generation.current;
      void request<Page>("notifications/operations", "POST", occurrence)
        .then(() => {
          setUnsavedNotice(null);
          setError("");
          void refresh().catch((e: Error) => setLoadError(e.message));
        })
        .catch((e: Error) => {
          setUnsavedNotice(occurrence);
          setError(`Notification could not be saved: ${e.message}`);
          if (reveal)
            toast.warning("Notification history could not be saved.", {
              id: "notice-save-failed",
              description: "Open Notifications to retry.",
            });
        });
    },
    [refresh],
  );
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    ++generation.current;
    try {
      await action();
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function toggleBrowser() {
    if (!settings) return;
    if (
      !settings.browser_enabled &&
      (await Notification.requestPermission()) !== "granted"
    ) {
      throw new Error(
        "Notifications are blocked or not allowed. Change this site's browser permission to enable them.",
      );
    }
    await request("notifications/settings", "PUT", {
      browser_enabled: !settings.browser_enabled,
    });
    toast.success(
      settings.browser_enabled
        ? "Browser notifications disabled."
        : "Browser notifications enabled.",
    );
  }
  async function checkUpdates() {
    const result = await request<Update>("updates/check", "POST", {
      reason: "manual",
    });
    if (result.checking) {
      manualCheck.current = true;
      toast.info("Checking for updates…", { id: "update-check" });
    } else reportUpdate(result);
  }
  async function allowBrowser() {
    if ((await Notification.requestPermission()) !== "granted") {
      throw new Error(
        "Notifications are blocked or not allowed. Change this site's browser permission to enable them.",
      );
    }
  }
  return (
    <Context.Provider value={{ notify, show, count: page.items.length }}>
      {children}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          aria-describedby={undefined}
          className="notification-drawer w-full sm:max-w-md"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            opener.current?.focus();
          }}
        >
          <SheetHeader>
            <SheetTitle>Notifications</SheetTitle>
          </SheetHeader>
          <div className="notification-items">
            <div className="content-stack">
              <Button
                size="sm"
                variant="outline"
                disabled={busy || !settings || !browserNoticesSupported()}
                onClick={() => void act(toggleBrowser)}
              >
                {settings?.browser_enabled
                  ? "Disable browser notifications"
                  : "Enable browser notifications"}
              </Button>
              {!browserNoticesSupported() && (
                <p>Browser notifications are unavailable here.</p>
              )}
              {settings?.browser_enabled &&
                browserNoticesSupported() &&
                Notification.permission !== "granted" && (
                  <p>
                    Allow notifications in this browser's site permissions to
                    receive desktop alerts here.
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => void act(allowBrowser)}
                    >
                      Allow in this browser
                    </Button>
                  </p>
                )}
            </div>
            <section className="content-stack" aria-label="Updates">
              <DetailHeading titleAs="h3" title="Updates" />
              <p>
                {updates
                  ? `Installed: ${updates.installed_version}`
                  : "Loading update status…"}
              </p>
              {updates?.last_success && (
                <p>
                  Last checked <Timestamp date={updates.last_success} />
                </p>
              )}
              {updates?.latest_version && (
                <p>Latest compatible: {updates.latest_version}</p>
              )}
              {updates?.error && <p role="status">{updates.error}</p>}
              <div className="actions">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || updates?.checking}
                  onClick={() => void act(checkUpdates)}
                >
                  {updates?.checking ? "Checking…" : "Check for updates"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !updates || updates.disabled_by_environment}
                  onClick={() =>
                    void act(async () => {
                      await request("updates/settings", "PUT", {
                        automatic: !updates?.automatic,
                      });
                      toast.success("Update preferences saved.");
                    })
                  }
                >
                  {updates?.automatic
                    ? "Disable automatic checks"
                    : "Enable automatic checks"}
                </Button>
              </div>
              {updates?.disabled_by_environment && (
                <p>
                  Automatic checks are disabled by the service configuration.
                </p>
              )}
            </section>
            {(error || loadError) && (
              <Alert variant="destructive">
                <AlertDescription>
                  <ContentStack>
                    <p>{error || loadError}</p>
                    {unsavedNotice && (
                      <p>
                        {unsavedNotice.title}: {unsavedNotice.message}
                      </p>
                    )}
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        unsavedNotice
                          ? notify(unsavedNotice)
                          : void act(() => refresh())
                      }
                    >
                      Retry
                    </Button>
                  </ContentStack>
                </AlertDescription>
              </Alert>
            )}
            {!page.items.length && <p className="muted">No notifications.</p>}
            {page.items.map((item) => (
              <Alert key={item.id}>
                <AlertTitle className="line-clamp-none">
                  {item.scope && (
                    <p className="detail-metadata">{item.scope}</p>
                  )}
                  <DetailHeading entry titleAs="h3" title={item.title} />
                </AlertTitle>
                <AlertDescription>
                  <ContentStack>
                    <p>{item.message}</p>
                    <span className="detail-metadata">
                      <Timestamp date={item.created_at} />
                    </span>
                    {item.commands.map((command) => (
                      <div key={command.label} className="content-stack">
                        <p>{command.label}</p>
                        <pre className="overflow-x-auto">
                          <code>{command.command}</code>
                        </pre>
                      </div>
                    ))}
                    <div className="actions">
                      {item.actions.map((action) => (
                        <Button
                          key={action.href}
                          size="sm"
                          variant="outline"
                          asChild
                        >
                          {action.href.startsWith("/") ? (
                            <WorkspaceLink
                              to={action.href}
                              onClick={() => setOpen(false)}
                            >
                              {action.label}
                            </WorkspaceLink>
                          ) : (
                            <a
                              href={action.href}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {action.label}
                            </a>
                          )}
                        </Button>
                      ))}
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        aria-label={`Dismiss ${item.title}`}
                        onClick={() =>
                          void act(() =>
                            request("notifications/dismiss", "POST", {
                              ids: [item.id],
                            }),
                          )
                        }
                      >
                        Dismiss
                      </Button>
                    </div>
                  </ContentStack>
                </AlertDescription>
              </Alert>
            ))}
            {!!page.items.length && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void act(() =>
                    request("notifications/dismiss", "POST", {
                      through: page.through,
                    }),
                  )
                }
              >
                Clear notifications
              </Button>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </Context.Provider>
  );
}
export function NotificationButton() {
  const { show, count } = useNotifications();
  return (
    <SidebarMenuButton
      className="relative"
      tooltip={count ? `Notifications (${count})` : "Notifications"}
      aria-label={count ? `Notifications (${count})` : "Notifications"}
      onClick={show}
    >
      <Bell aria-hidden="true" />
      <span data-sidebar="label">
        Notifications{count ? ` (${count})` : ""}
      </span>
      <CountBadge count={count} label="Notifications" />
    </SidebarMenuButton>
  );
}
