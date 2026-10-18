import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";
import {
  useHost,
  type Announcement,
  type UpdateStatus,
} from "./HostCapabilities";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";
import type { components } from "./api-schema";

export type AnnouncementSettings =
  components["schemas"]["AnnouncementSettings"];
type Delivery = components["schemas"]["Delivery"];
const Context = createContext<{
  updates: UpdateStatus | null;
  checkUpdates(): Promise<void>;
  configureUpdates(automatic: boolean): Promise<void>;
} | null>(null);
export function useAnnouncements() {
  const value = useContext(Context);
  if (!value) throw new Error("Announcement provider missing");
  return value;
}

export function visibleSources(): string[] {
  if (document.visibilityState !== "visible" || !document.hasFocus()) return [];
  return [
    ...document.querySelectorAll<HTMLElement>("[data-announcement-source]"),
  ]
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      const left = Math.max(0, rect.left),
        right = Math.min(innerWidth, rect.right);
      const topEdge = Math.max(0, rect.top),
        bottom = Math.min(innerHeight, rect.bottom);
      if (right <= left || bottom <= topEdge) return false;
      const top = document.elementFromPoint(
        (left + right) / 2,
        (topEdge + bottom) / 2,
      );
      return top !== null && element.contains(top);
    })
    .flatMap((element) => element.dataset.announcementSource!.split(" "))
    .slice(0, 100);
}

export function groupAnnouncements(items: Announcement[]): Announcement[] {
  const updates = items.filter((item) => item.kind === "update");
  const work = items.filter((item) => item.kind === "work");
  if (work.length <= 3) return [...work, ...updates];
  const workspaces = new Set(work.map((item) => item.workspace_id));
  const first = work[0];
  return [
    {
      ...first,
      source_key: `group:${first.id}`,
      title: `${work.length} items need your attention`,
      message:
        workspaces.size === 1
          ? "Open Inbox to review them."
          : `Across ${workspaces.size} workspaces. Open a workspace’s Inbox to review them.`,
      href:
        workspaces.size === 1
          ? `/workspaces/${encodeURIComponent(first.workspace_id!)}/inbox`
          : "/",
    },
    ...updates,
  ];
}

export function AnnouncementProvider({
  navigate,
  children,
}: {
  navigate: (href: string) => void;
  children: ReactNode;
}) {
  const host = useHost();
  const [updates, setUpdates] = useState<UpdateStatus | null>(null);
  const [clientId] = useState(() => crypto.randomUUID());
  const navigation = useRef(navigate);
  const manual = useRef<"requesting" | "waiting" | null>(null);
  const manualVersion = useRef<string | null>(null);
  const updateGeneration = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    navigation.current = navigate;
  }, [navigate]);
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let inFlight = false;
    const controller = new AbortController();
    async function poll() {
      if (stopped || inFlight) return;
      clearTimeout(timer);
      inFlight = true;
      let failed = false;
      try {
        const delivery = await request<Delivery>(
          "announcements/poll",
          "POST",
          {
            client_id: clientId,
            active:
              document.visibilityState === "visible" && document.hasFocus(),
            system: host.notifications.kind,
            permitted: host.notifications.permission() === "granted",
            visible_sources: visibleSources(),
            manual_update: !!manual.current,
          },
          controller.signal,
          15000,
        );
        const visible = new Set(visibleSources());
        const activeNow =
          document.visibilityState === "visible" && document.hasFocus();
        const channel = activeNow
          ? "toast"
          : delivery.system_allowed
            ? "system"
            : null;
        if (!stopped)
          for (const item of groupAnnouncements(
            (delivery.items ?? []).filter(
              (item) =>
                !visible.has(item.source_key) &&
                !(
                  item.kind === "update" &&
                  (manual.current ||
                    item.source_key === `update:${manualVersion.current}`)
                ),
            ),
          )) {
            if (channel === "toast") {
              toast.info(item.title, {
                id: `announcement:${item.id}`,
                description: item.message,
                duration: 8000,
                action: {
                  label: item.kind === "update" ? "View update" : "Open",
                  onClick: () => navigation.current(item.href),
                },
              });
            } else if (channel === "system") {
              await host.notifications
                .present(item, (href) => navigation.current(href))
                .catch(() => {
                  // The durable destination remains available; do not replay uncertain delivery.
                });
            }
          }
      } catch (error) {
        failed = true;
        if (!stopped)
          reportError(error, "Could not check for new alerts", true);
      } finally {
        inFlight = false;
        if (!stopped) {
          if (failed) timer = setTimeout(() => void poll(), 2000);
          else void poll();
        }
      }
    }
    const wake = () => void poll();
    window.addEventListener("focus", wake);
    window.addEventListener("blur", wake);
    document.addEventListener("visibilitychange", wake);
    void poll();
    return () => {
      mounted.current = false;
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener("focus", wake);
      window.removeEventListener("blur", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [clientId, host]);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function read() {
      try {
        const generation = updateGeneration.current;
        const value = await host.updates.status(controller.signal);
        if (
          !stopped &&
          generation === updateGeneration.current &&
          manual.current !== "requesting"
        )
          setUpdates(value);
      } catch (error) {
        if (!stopped) reportError(error, "Could not load update status", true);
      }
      if (!stopped) timer = setTimeout(() => void read(), 3000);
    }
    void host.updates.check("startup").catch(() => {});
    void read();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [host]);
  useEffect(() => {
    if (manual.current !== "waiting" || !updates || updates.checking) return;
    manual.current = null;
    manualVersion.current = updates.available_version ?? null;
    if (updates.error) toast.error(updates.error, { id: "update-check" });
    else
      toast.info(
        updates.available_version
          ? `Flowfield ${updates.available_version} is available.`
          : "No newer compatible release found.",
        { id: "update-check" },
      );
  }, [updates]);
  async function checkUpdates() {
    if (manual.current) return;
    manual.current = "requesting";
    ++updateGeneration.current;
    toast.info("Checking for updates…", { id: "update-check" });
    try {
      const value = await host.updates.check("manual", clientId);
      manual.current = "waiting";
      if (mounted.current) setUpdates(value);
    } catch (error) {
      manual.current = null;
      toast.dismiss("update-check");
      throw error;
    }
  }
  async function configureUpdates(automatic: boolean) {
    const generation = ++updateGeneration.current;
    const value = await host.updates.configure(automatic);
    if (generation === updateGeneration.current && mounted.current)
      setUpdates(value);
  }
  return (
    <Context.Provider value={{ updates, checkUpdates, configureUpdates }}>
      {children}
    </Context.Provider>
  );
}
