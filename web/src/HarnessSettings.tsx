import { useCallback, useEffect, useState } from "react";
import { Settings } from "lucide-react";
import type { components } from "./api-schema";
import { request } from "./workspace";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import {
  ContentStack,
  DetailHeading,
  DetailSection,
  Disclosure,
} from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { ConfirmButton } from "./ConfirmButton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SidebarMenuButton, useSidebar } from "@/components/ui/sidebar";
import "./harness-settings.css";
import { harnessNames as names } from "./HarnessModels";

type Status = components["schemas"]["HarnessStatus"];
type Registration = components["schemas"]["HarnessRegistration"];
type Kind = Registration["harness"];

export function SettingsLink({ active }: { active: boolean }) {
  const { setOpenMobile } = useSidebar();
  return (
    <SidebarMenuButton asChild tooltip="Settings" isActive={active}>
      <WorkspaceLink
        to="/settings/harnesses"
        aria-label="Settings"
        aria-current={active ? "page" : undefined}
        onClick={() => setOpenMobile(false)}
      >
        <Settings />
        <span data-sidebar="label">Settings</span>
      </WorkspaceLink>
    </SidebarMenuButton>
  );
}

function setupLabel(value: Status) {
  if (value.catalog_ownership?.status === "uncertain")
    return "Discovery needs attention";
  if (value.catalog_ownership) return "Discovering native choices";
  if (value.installing) return "Installing bridge";
  if (!value.native_installed) return "Native harness missing";
  if (!value.bridge_installed) return "Bridge needed";
  if (!value.config_available) return "Configuration directory missing";
  if (value.authentication === "signed-out") return "Sign-in required";
  if (value.problems.includes("adapter_not_available"))
    return "Integration in progress";
  return value.checked && value.authentication === "authenticated"
    ? "Setup checked"
    : "Setup detected";
}

function HarnessEntry({
  kind,
  onDirty,
}: {
  kind: Kind;
  onDirty: (kind: Kind, dirty: boolean) => void;
}) {
  const path = `harnesses/${kind}`;
  const resource = useResource<Status>(path, 0);
  const [action, setAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dirty = useCallback(
    (value: boolean) => onDirty(kind, value),
    [kind, onDirty],
  );
  const read = async () => {
    const latest = await request<Status>(path);
    resource.invalidate();
    resource.setData(latest);
    resource.setError("");
    return latest.registration;
  };
  const editor = useRecordEditor<
    Registration,
    { executable: string; config_directory: string }
  >({
    incoming: resource.data?.registration,
    fields: (value) => ({
      executable: value?.executable ?? "",
      config_directory: value?.config_directory ?? "",
    }),
    path: () => path,
    saved: async (_record, method) => {
      if (method === "GET") return;
      await read().catch((error: Error) => {
        resource.invalidate();
        resource.setData(null);
        setError(error.message);
      });
    },
    read,
    onDirty: dirty,
  });
  const { element: harnessElement } = editor;
  const status = resource.data;
  async function operate(
    operation: "check" | "install" | "catalog/confirm-stopped",
    id?: string,
  ) {
    setAction(operation);
    setError("");
    setNotice("");
    try {
      const result = await request<Status>(
        `${path}/${operation}`,
        "POST",
        id ? { id } : undefined,
        undefined,
        180000,
      );
      resource.invalidate();
      resource.setData(result);
      resource.setError("");
      setNotice(
        operation === "install"
          ? "Bridge installed. Check the saved setup next."
          : operation === "check"
            ? "Setup checked. Model access still needs a successful turn."
            : "Discovery hold cleared. Native choices can be loaded again.",
      );
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setAction("");
    }
  }
  const busy = !!action || editor.busy || !!status?.installing;
  return (
    <section
      ref={harnessElement}
      className="harness-entry"
      aria-label={`${names[kind]} settings`}
    >
      <ContentStack space="section">
        <DetailHeading
          title={names[kind]}
          status={
            status
              ? setupLabel(status)
              : resource.loading
                ? "Loading setup…"
                : "Setup unavailable"
          }
        />
        {status && (
          <>
            {!status.native_installed && (
              <p>
                Install {names[kind]} on the service host, then check the setup
                or supply its executable below.
              </p>
            )}
            {status.authentication === "signed-out" && (
              <p>
                Sign in with {names[kind]} on the service host, then check
                again.
              </p>
            )}
            {status.problems.includes("adapter_not_available") && (
              <p>
                Claude Code setup is available. Managed model selection is still
                being integrated.
              </p>
            )}
            <div className="actions">
              {!status.bridge_installed && (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => void operate("install")}
                >
                  {action === "install" || status.installing
                    ? "Installing bridge…"
                    : "Install bridge"}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                disabled={
                  busy ||
                  editor.dirty ||
                  !status.native_installed ||
                  !status.config_available
                }
                onClick={() => void operate("check")}
              >
                {action === "check" ? "Checking setup…" : "Check saved setup"}
              </Button>
              {(status.installing || status.catalog_ownership) && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!action || editor.busy}
                  onClick={() =>
                    void read().catch((error: Error) => setError(error.message))
                  }
                >
                  Reload status
                </Button>
              )}
            </div>
            <p className="detail-metadata">
              Checks read native version and sign-in status. They send no model
              prompt and change no native configuration.
            </p>
            {status.catalog_ownership && (
              <Alert>
                <AlertTitle>
                  {status.catalog_ownership.status === "uncertain"
                    ? "Discovery cleanup is unconfirmed"
                    : "Native discovery is running"}
                </AlertTitle>
                <AlertDescription>
                  <ContentStack>
                    <p>
                      {status.catalog_ownership.operation === "models"
                        ? "Model"
                        : "Command"}{" "}
                      discovery started{" "}
                      <Timestamp date={status.catalog_ownership.started_at} />
                      {status.catalog_ownership.project_id
                        ? ` for project ${status.catalog_ownership.project_id}`
                        : ""}
                      .
                    </p>
                    {status.catalog_ownership.status === "uncertain" ? (
                      <>
                        <p>
                          Inspect the service host and stop remaining work from
                          this discovery, including startup hooks and background
                          tools. Process absence alone does not confirm cleanup.
                        </p>
                        <ConfirmButton
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          title={`Confirm ${names[kind]} discovery stopped?`}
                          description="Confirm only after stopping remaining work from this interrupted discovery on the service host. This clears its hold and permits a new discovery."
                          action={() =>
                            void operate(
                              "catalog/confirm-stopped",
                              status.catalog_ownership!.id,
                            )
                          }
                        >
                          Confirm discovery stopped
                        </ConfirmButton>
                      </>
                    ) : (
                      <p>
                        Wait for discovery to finish, then reload status.
                        Retrying does not start another owner.
                      </p>
                    )}
                  </ContentStack>
                </AlertDescription>
              </Alert>
            )}
            <DetailSection title="Native configuration">
              <p className="detail-metadata">
                Optional overrides on the service host. Leave blank to use
                native defaults. Accounts and credentials stay with{" "}
                {names[kind]}.
              </p>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void editor.run("PUT", {
                    expected_revision: editor.loaded?.revision,
                    executable: editor.values.executable || null,
                    config_directory: editor.values.config_directory || null,
                  });
                }}
              >
                <fieldset
                  disabled={busy}
                  className="content-stack"
                  data-space="content"
                >
                  <Label className="field block">
                    Executable override
                    <Input
                      value={editor.values.executable}
                      placeholder={
                        status.launch.native_executable ??
                        "Absolute service-host path"
                      }
                      onChange={(event) =>
                        editor.change("executable", event.target.value)
                      }
                    />
                  </Label>
                  <Label className="field block">
                    Configuration directory override
                    <Input
                      aria-label="Configuration directory override"
                      aria-describedby={`harness-${kind}-config-help`}
                      value={editor.values.config_directory}
                      placeholder={status.launch.config_directory}
                      onChange={(event) =>
                        editor.change("config_directory", event.target.value)
                      }
                    />
                    <span
                      id={`harness-${kind}-config-help`}
                      className="detail-metadata"
                    >
                      Directory for{" "}
                      {kind === "codex"
                        ? "CODEX_HOME; not config.toml"
                        : "CLAUDE_CONFIG_DIR; not settings.json"}
                      .
                    </span>
                  </Label>
                  <div className="actions">
                    <Button
                      size="sm"
                      disabled={
                        !editor.dirty || editor.newer || editor.conflict
                      }
                    >
                      {editor.busy ? "Saving…" : "Save paths"}
                    </Button>
                    {editor.dirty && (
                      <Button
                        size="sm"
                        variant="outline"
                        type="button"
                        onClick={editor.cancel}
                      >
                        Cancel
                      </Button>
                    )}
                  </div>
                </fieldset>
              </form>
              <EditorFeedback state={editor} />
            </DetailSection>
            <Disclosure summary="Detected setup">
              <dl className="harness-facts">
                <dt>Native executable</dt>
                <dd>{status.launch.native_executable ?? "Not detected"}</dd>
                <dt>Configuration directory</dt>
                <dd>
                  {status.launch.config_directory} (
                  {status.launch.config_source})
                </dd>
                <dt>Native version</dt>
                <dd>{status.native_version ?? "Run Check saved setup"}</dd>
                <dt>Bridge</dt>
                <dd>{status.launch.bridge_version ?? "Not installed"}</dd>
                <dt>Sign-in</dt>
                <dd>
                  {status.authentication === "unknown"
                    ? "Not verified"
                    : status.authentication === "authenticated"
                      ? "Signed in"
                      : "Signed out"}
                </dd>
                <dt>Model access</dt>
                <dd>Requires a successful model turn</dd>
                <dt>Managed roles</dt>
                <dd>
                  {status.problems.includes("adapter_not_available")
                    ? "Not available yet"
                    : "Coordinator and task workers"}
                </dd>
              </dl>
            </Disclosure>
          </>
        )}
        {(error || resource.error) && (
          <Alert variant="destructive">
            <AlertDescription>{error || resource.error}</AlertDescription>
          </Alert>
        )}
        {!status && !resource.loading && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void read().catch((error: Error) => setError(error.message))
            }
          >
            Reload setup
          </Button>
        )}
        {notice && <p role="status">{notice}</p>}
      </ContentStack>
    </section>
  );
}

export function HarnessSettings({
  onDirty,
}: {
  onDirty: (value: boolean) => void;
}) {
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const changed = useCallback(
    (kind: Kind, value: boolean) =>
      setDirty((current) =>
        current[kind] === value ? current : { ...current, [kind]: value },
      ),
    [],
  );
  useEffect(() => {
    onDirty(Object.values(dirty).some(Boolean));
    return () => onDirty(false);
  }, [dirty, onDirty]);
  return (
    <section className="harness-settings-page" aria-label="Flowfield settings">
      <ContentStack space="section" className="harness-settings-content">
        <h1>Settings</h1>
        <ContentStack>
          <DetailHeading title="Harnesses" titleAs="h2" />
          <p>
            Use any supported harness you have set up. Project Coordinator and
            Workers settings choose their models independently.
          </p>
          <p className="detail-metadata">
            Paths and installations belong to the Flowfield service host.
            Running work keeps its captured settings.
          </p>
        </ContentStack>
        <HarnessEntry kind="codex" onDirty={changed} />
        <HarnessEntry kind="claude-code" onDirty={changed} />
      </ContentStack>
    </section>
  );
}
