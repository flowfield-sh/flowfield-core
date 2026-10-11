import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { useAnnouncements } from "./Announcements";
import { useHost } from "./HostCapabilities";
import { reportError } from "./requestFeedback";

export function UpdateSettings() {
  const { updates, checkUpdates, configureUpdates } = useAnnouncements();
  const host = useHost();
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
      <DetailHeading title="Updates" />
      {!updates ? (
        <p>Loading update status…</p>
      ) : (
        <>
          <p>Installed version: {updates.installed_version}</p>
          {updates.last_success && (
            <p className="detail-metadata">
              Last checked <Timestamp date={updates.last_success} />
            </p>
          )}
          {updates.error && (
            <Alert>
              <AlertTitle>Update check needs attention</AlertTitle>
              <AlertDescription>{updates.error}</AlertDescription>
            </Alert>
          )}
          {updates.available_version ? (
            <ContentStack>
              <DetailHeading
                titleAs="h3"
                title={`Flowfield ${updates.available_version} is available`}
              />
              <a
                className="text-link"
                href={updates.release_notes}
                target="_blank"
                rel="noreferrer"
              >
                Release notes
              </a>
              {updates.treatment === "package" && (
                <>
                  <p>
                    Stop Flowfield, upgrade using your installation method, then
                    restart. Your workspace and saved queue settings are
                    retained.
                  </p>
                  {(updates.commands ?? []).map((command) => (
                    <div className="content-stack min-w-0" key={command.label}>
                      <p>{command.label}</p>
                      <pre className="overflow-x-auto">
                        <code>{command.command}</code>
                      </pre>
                    </div>
                  ))}
                </>
              )}
              {host.updates.actions(updates).map((action) => (
                <Button
                  key={action.id}
                  disabled={busy}
                  onClick={() => void act(action.run)}
                >
                  {action.label}
                </Button>
              ))}
            </ContentStack>
          ) : (
            <p>
              {updates.checking
                ? "Checking for updates…"
                : updates.last_success && !updates.error
                  ? "No newer compatible release found."
                  : "Check for a compatible Flowfield release."}
            </p>
          )}
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              disabled={busy || updates.disabled_by_environment}
              checked={updates.automatic}
              onChange={(e) => {
                const value = e.target.checked;
                void act(async () => {
                  await configureUpdates(value);
                  toast.success("Update preferences saved.");
                });
              }}
            />
            Check for updates automatically
          </label>
          {updates.disabled_by_environment && (
            <p className="detail-metadata">
              Automatic checks are disabled by the service configuration.
            </p>
          )}
        </>
      )}
      <div className="actions">
        <Button
          variant="outline"
          disabled={busy || updates?.checking}
          onClick={() => void act(checkUpdates)}
        >
          {updates?.checking ? "Checking…" : "Check for updates"}
        </Button>
      </div>
    </ContentStack>
  );
}
