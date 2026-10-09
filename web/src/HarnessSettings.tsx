import { useCallback, useEffect, useState } from "react";
import { Settings } from "lucide-react";
import type { components } from "./api-schema";
import { request } from "./workspace";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { ContentStack, DetailHeading, Disclosure } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { ConfirmButton } from "./ConfirmButton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SidebarMenuButton, useSidebar } from "@/components/ui/sidebar";
import "./harness-settings.css";
import { harnessNames as names } from "./HarnessModels";
import { HarnessLogo } from "./HarnessSelect";

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
  if (!value.native_installed) return "Native harness missing";
  if (!value.config_available) return "Configuration directory missing";
  if (value.authentication === "signed-out") return "Sign-in required";
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
    operation: "check" | "catalog/confirm-stopped",
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
        operation === "check"
          ? "Setup checked."
          : "Discovery hold cleared. Native choices can be loaded again.",
      );
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setAction("");
    }
  }
  const busy = !!action || editor.busy;
  return (
    <section
      ref={harnessElement}
      className="harness-entry"
      aria-label={`${names[kind]} settings`}
    >
      <ContentStack space="section">
        <DetailHeading
          title={
            <span className="harness-name">
              <HarnessLogo kind={kind} />
              {names[kind]}
            </span>
          }
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
            <div className="actions">
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
              {status.catalog_ownership && (
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
            <Disclosure summary="Path overrides">
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
                    Executable path
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
                    Configuration directory
                    <Input
                      aria-label="Configuration directory"
                      value={editor.values.config_directory}
                      placeholder={status.launch.config_directory}
                      onChange={(event) =>
                        editor.change("config_directory", event.target.value)
                      }
                    />
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
            </Disclosure>
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
                <dt>Sign-in</dt>
                <dd>
                  {status.authentication === "unknown"
                    ? status.checked
                      ? "Could not determine sign-in status"
                      : "Not checked — run Check saved setup"
                    : status.authentication === "authenticated"
                      ? "Signed in"
                      : "Signed out"}
                </dd>
                <dt>Model access</dt>
                <dd>Requires a successful model turn</dd>
                <dt>Managed roles</dt>
                <dd>Coordinator and task workers</dd>
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
          <p className="detail-metadata">
            Use Codex or Claude Code installed on the service host. Choose
            models in project settings; override detected paths only when
            needed.
          </p>
        </ContentStack>
        <HarnessEntry kind="codex" onDirty={changed} />
        <HarnessEntry kind="claude-code" onDirty={changed} />
      </ContentStack>
    </section>
  );
}
