import {
  createContext,
  createElement,
  useContext,
  type ReactNode,
} from "react";
import type { components } from "./api-schema";
import { request } from "./workspace";

export type UpdateStatus = components["schemas"]["UpdateStatus"];
export type Announcement = components["schemas"]["Announcement"];
export type SystemPermission = NotificationPermission | "unavailable";

export interface NotificationCapability {
  kind: "browser" | "native";
  permission(): SystemPermission;
  requestPermission(): Promise<SystemPermission>;
  present(
    item: Pick<Announcement, "title" | "message" | "source_key" | "href">,
    activate: (href: string) => void,
  ): Promise<void>;
}
export interface UpdateCapability {
  status(signal?: AbortSignal): Promise<UpdateStatus>;
  check(reason: "startup" | "manual", clientId?: string): Promise<UpdateStatus>;
  configure(automatic: boolean): Promise<UpdateStatus>;
  // The installer supplies only actions it owns. Package updates have none.
  actions(
    status: UpdateStatus,
  ): { id: string; label: string; run(): Promise<void> }[];
}
export type HostCapabilities = {
  notifications: NotificationCapability;
  updates: UpdateCapability;
};

export const browserHost: HostCapabilities = {
  notifications: {
    kind: "browser",
    permission: () =>
      window.isSecureContext && "Notification" in window
        ? Notification.permission
        : "unavailable",
    requestPermission: async () =>
      window.isSecureContext && "Notification" in window
        ? Notification.permission === "default"
          ? Notification.requestPermission()
          : Notification.permission
        : "unavailable",
    async present(item, activate) {
      if (
        !window.isSecureContext ||
        !("Notification" in window) ||
        Notification.permission !== "granted"
      )
        throw new Error("Allow notifications in this browser's site settings.");
      const notification = new Notification(item.title, {
        body: item.message,
        tag: item.source_key,
      });
      notification.onclick = () => {
        window.focus();
        activate(item.href);
        notification.close();
      };
    },
  },
  updates: {
    status: (signal) =>
      request<UpdateStatus>("updates", "GET", undefined, signal),
    check: (reason, clientId) =>
      request<UpdateStatus>("updates/check", "POST", {
        reason,
        client_id: clientId,
      }),
    configure: (automatic) =>
      request<UpdateStatus>("updates/settings", "PUT", { automatic }),
    actions: () => [],
  },
};
const HostContext = createContext<HostCapabilities>(browserHost);
// Native composition injects capabilities here, never through project content or a URL flag.
export function HostProvider({
  host,
  children,
}: {
  host: HostCapabilities;
  children: ReactNode;
}) {
  return createElement(HostContext.Provider, { value: host }, children);
}
export const useHost = () => useContext(HostContext);
