import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { StatusNotice } from "./StatusNotice";
import { Timestamp } from "./Timestamp";
import { useAnnouncements } from "./Announcements";
import { useHost } from "./HostCapabilities";
import { reportError } from "./requestFeedback";

export function UpdateSettings() {
  const { updates, checkUpdates, configureUpdates } = useAnnouncements();
  const host = useHost();
  const updateActions = updates ? host.updates.actions(updates) : [];
  const [busy, setBusy] = useState(false);
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      reportError(error, "Could not update Flowfield settings");
    } finally {
      setBusy(false);
    }
  }
  return (
    <ContentStack
      space="section"
      data-announcement-source={
        updates?.available_version
          ? `update:${updates.available_version}`
          : undefined
      }
    >
      {!updates ? (
        <StatusNotice title="Loading update status…" tone="progress" />
      ) : (
        <>
          <ContentStack>
            <SettingsCheckbox
              disabled={busy || updates.disabled_by_environment}
              checked={updates.automatic}
              hint={
                updates.disabled_by_environment
                  ? "Automatic checks are disabled by the service configuration."
                  : "Check in the background. Installing an update remains a separate action."
              }
              onChange={(e) => {
                const value = e.target.checked;
                void act(async () => {
                  await configureUpdates(value);
                  toast.success("Update preferences saved.");
                });
              }}
            >
              Check for updates automatically
            </SettingsCheckbox>
            <StatusNotice
              title={
                updates.checking
                  ? "Checking for updates…"
                  : updates.error
                    ? "Update check needs attention"
                    : updates.available_version
                      ? `Flowfield ${updates.available_version} is available`
                      : updates.last_success
                        ? "Flowfield is up to date"
                        : "Ready to check for updates"
              }
              tone={
                updates.checking
                  ? "progress"
                  : updates.error
                    ? "attention"
                    : updates.available_version
                      ? "neutral"
                      : updates.last_success
                        ? "success"
                        : "neutral"
              }
              actions={
                <>
                  <Button
                    variant="outline"
                    disabled={busy || updates.checking}
                    onClick={() => void act(checkUpdates)}
                  >
                    {updates.checking ? "Checking…" : "Check for updates"}
                  </Button>
                  {updates.available_version && (
                    <a
                      className="text-link"
                      href={updates.release_notes}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Release notes
                    </a>
                  )}
                </>
              }
            >
              <p>
                Installed version: {updates.installed_version}
                {updates.last_success && (
                  <>
                    {" "}
                    · Last checked <Timestamp date={updates.last_success} />
                  </>
                )}
              </p>
              {updates.error ? (
                <p>{updates.error}</p>
              ) : (
                !updates.checking &&
                !updates.available_version && (
                  <p>
                    {updates.last_success
                      ? "No newer compatible release found."
                      : "Check for a compatible Flowfield release."}
                  </p>
                )
              )}
            </StatusNotice>
          </ContentStack>
          {updates.available_version && (
            <DetailSection title="Install update">
              {updates.error && (
                <p>Previously found: Flowfield {updates.available_version}</p>
              )}
              {updates.treatment === "package" && (
                <>
                  <p>
                    Stop Flowfield, upgrade using your installation method, then
                    restart. Your workspace and saved queue settings are
                    retained.
                  </p>
                  {(updates.commands ?? []).map((command) => (
                    <ContentStack space="tight" key={command.label}>
                      <strong>{command.label}</strong>
                      <pre className="overflow-x-auto">
                        <code>{command.command}</code>
                      </pre>
                    </ContentStack>
                  ))}
                </>
              )}
              {updateActions.length > 0 && (
                <div className="actions">
                  {updateActions.map((action) => (
                    <Button
                      key={action.id}
                      disabled={busy}
                      onClick={() => void act(action.run)}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              )}
            </DetailSection>
          )}
        </>
      )}
    </ContentStack>
  );
}
