import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { NativeSelect } from "@/components/ui/native-select";
import { ContentStack, DetailSection } from "./DetailLayout";
import { ConfirmButton } from "./ConfirmButton";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { ResourceRetry } from "./ResourceRetry";
import { CheckEvidence } from "./CheckEvidence";
import { StatusNotice } from "./StatusNotice";
import { useResource } from "./useResource";
import { useInspectionSettings } from "./useInspectionSettings";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import type { components } from "./api-schema";

type Settings = components["schemas"]["IntegrationSettings"];
type Check = components["schemas"]["SetupCheck"];
type Scope = "setup" | "validation";
type Tab = "delivery" | Scope;
type Draft = { revision: number; text: string; timeout: number };
const tabs = ["delivery", "setup", "validation"] as const;
const lines = (value: string) =>
  value
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);
const checking = (value: Check | null) => value?.status === "checking";
function fields(tab: Tab, settings: Settings | null): Draft {
  return {
    revision: settings?.revision ?? 1,
    text:
      tab === "delivery"
        ? (settings?.target_branch ?? "")
        : ((tab === "setup"
            ? settings?.setup_commands
            : settings?.checks
          )?.join("\n") ?? ""),
    timeout:
      (tab === "setup"
        ? settings?.setup_timeout_seconds
        : settings?.check_timeout_seconds) ?? (tab === "setup" ? 120 : 60),
  };
}
function useValidation(
  projectId: string,
  scope: Scope,
  revision: number,
  refresh: unknown,
) {
  const path = `projects/${projectId}/setup-validation`;
  const key = useMemo(() => ({ revision, refresh }), [revision, refresh]);
  const resource = useResource<Check | null>(
    `${path}?scope=${scope}`,
    key,
    10000,
    { projectId, isActive: checking },
  );
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!checking(resource.data)) return;
    const timer = window.setInterval(resource.revalidate, 1000);
    return () => window.clearInterval(timer);
  }, [resource.data, resource.revalidate]);
  async function validate() {
    setBusy(true);
    try {
      const value = await request<Check>(path, "POST", {
        expected_revision: revision,
        scope,
      });
      resource.invalidate();
      resource.setData(value);
      resource.retry();
    } catch (error) {
      reportError(error, "Could not start validation");
      resource.retry();
    } finally {
      setBusy(false);
    }
  }
  return { ...resource, busy: busy || checking(resource.data), validate };
}
function ValidationResult({
  scope,
  resource,
}: {
  scope: Scope;
  resource: ReturnType<typeof useValidation>;
}) {
  const value = resource.data;
  if (!value && !resource.busy && !resource.error) return null;
  return (
    <DetailSection
      settings
      title={scope === "setup" ? "Setup validation" : "Validation results"}
    >
      <ResourceRetry resources={[resource]} />
      {(value || resource.busy) && (
        <StatusNotice
          title={
            resource.busy
              ? "Validating…"
              : value?.stale
                ? "Validation is out of date"
                : value?.status === "passed"
                  ? "Validation passed"
                  : value?.status === "uncertain"
                    ? "Cleanup needs attention"
                    : "Validation failed"
          }
          tone={
            resource.busy
              ? "progress"
              : value?.stale ||
                  value?.status !== "passed" ||
                  value.checkout_problem
                ? "attention"
                : "success"
          }
        >
          {resource.busy && (
            <p>
              {scope === "setup"
                ? "Running setup commands in a separate copy."
                : "Running saved setup, then validation commands in a separate copy."}
            </p>
          )}
          {value?.stale && (
            <p>Relevant settings or the destination changed. Validate again.</p>
          )}
          {value?.problem && <p>{value.problem}</p>}
          {value?.checkout_problem && <p>{value.checkout_problem}</p>}
        </StatusNotice>
      )}
      {value && (
        <>
          <CheckEvidence
            checks={value.setup}
            title="Setup output"
            logBase={`${pathFor(value)}/setup`}
          />
          <CheckEvidence
            checks={value.checks}
            title="Validation output"
            logBase={`${pathFor(value)}/checks`}
          />
        </>
      )}
    </DetailSection>
  );
}
function pathFor(value: Check) {
  return `projects/${value.project_id}/setup-validation/${value.id}/logs`;
}

export function ProjectConfiguration({
  projectId,
  active,
  panelId,
  onDirty,
  refresh,
}: {
  projectId: string;
  active: string;
  panelId: string;
  onDirty: (value: boolean) => void;
  refresh: unknown;
}) {
  const id = useId();
  const path = `projects/${projectId}/integration`;
  const resource = useResource<Settings>(path, refresh);
  const [drafts, setDrafts] = useState<Partial<Record<Tab, Draft>>>({});
  const [busy, setBusy] = useState<Tab | "execution" | null>(null);
  const execution = useInspectionSettings(projectId, refresh);
  const setup = useValidation(
    projectId,
    "setup",
    resource.data?.revision ?? 1,
    refresh,
  );
  const validation = useValidation(
    projectId,
    "validation",
    resource.data?.revision ?? 1,
    refresh,
  );
  const dirty = tabs.some((tab) => {
    const saved = fields(tab, resource.data),
      draft = drafts[tab];
    return (
      draft && (draft.text !== saved.text || draft.timeout !== saved.timeout)
    );
  });
  useEffect(() => {
    onDirty(dirty || execution.dirty || !!busy);
    return () => onDirty(false);
  }, [dirty, execution.dirty, busy, onDirty]);
  async function save(tab: Tab) {
    const draft = drafts[tab] ?? fields(tab, resource.data);
    if (!resource.data) return;
    setBusy(tab);
    try {
      const changes =
        tab === "delivery"
          ? { target_branch: draft.text }
          : tab === "setup"
            ? {
                setup_commands: lines(draft.text),
                setup_timeout_seconds: draft.timeout,
              }
            : {
                checks: lines(draft.text),
                check_timeout_seconds: draft.timeout,
              };
      const value = await request<Settings>(path, "PUT", {
        expected_revision: draft.revision,
        ...changes,
      });
      resource.invalidate();
      resource.setData(value);
      setDrafts((current) => {
        const next = { ...current };
        delete next[tab];
        for (const key of tabs)
          if (next[key]?.revision === draft.revision)
            next[key] = { ...next[key]!, revision: value.revision };
        return next;
      });
      toast.success("Changes saved.");
    } catch (error) {
      reportError(error, "Could not save changes");
      resource.retry();
    } finally {
      setBusy(null);
    }
  }
  return (
    <>
      {tabs.map((tab) => {
        const value = drafts[tab] ?? fields(tab, resource.data),
          saved = fields(tab, resource.data);
        const changed =
          (tab === "delivery" &&
            resource.data?.revision === 1 &&
            !!value.text) ||
          value.text !== saved.text ||
          value.timeout !== saved.timeout;
        const stale =
          !!drafts[tab] && value.revision !== resource.data?.revision;
        const check = tab === "setup" ? setup : validation;
        const prerequisiteDirty =
          (!!drafts.delivery &&
            drafts.delivery.text !== fields("delivery", resource.data).text) ||
          (tab === "validation" &&
            !!drafts.setup &&
            (drafts.setup.text !== fields("setup", resource.data).text ||
              drafts.setup.timeout !== fields("setup", resource.data).timeout));
        return (
          <div
            key={tab}
            role="tabpanel"
            id={`${panelId}-${tab}`}
            aria-labelledby={`${panelId}-${tab}-tab`}
            hidden={active !== tab}
            className="content-stack"
            data-space="settings"
          >
            <ResourceRetry resources={[resource]} />
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save(tab);
              }}
            >
              <fieldset
                disabled={!!busy || !resource.data}
                className="content-stack"
                data-space="section"
              >
                <ContentStack space="tight">
                  <Label className="field block">
                    {tab === "delivery"
                      ? "Destination branch"
                      : tab === "setup"
                        ? "Setup commands"
                        : "Validation commands"}
                    {tab === "delivery" ? (
                      <Input
                        required
                        value={value.text}
                        aria-describedby={`${id}-${tab}-help`}
                        onChange={(e) =>
                          setDrafts({
                            ...drafts,
                            [tab]: { ...value, text: e.target.value },
                          })
                        }
                      />
                    ) : (
                      <Textarea
                        aria-label={
                          tab === "setup"
                            ? "Setup commands"
                            : "Validation commands"
                        }
                        rows={4}
                        value={value.text}
                        aria-describedby={`${id}-${tab}-help`}
                        onChange={(e) =>
                          setDrafts({
                            ...drafts,
                            [tab]: { ...value, text: e.target.value },
                          })
                        }
                      />
                    )}
                  </Label>
                  <p id={`${id}-${tab}-help`} className="detail-metadata">
                    {tab === "delivery"
                      ? "Approved code updates this local branch. Flowfield does not push or deploy it."
                      : tab === "setup"
                        ? "Install dependencies and build each work copy. One command per line; keep source files unchanged."
                        : "Checks Workers can run while developing and Flowfield runs before review. One command per line."}
                  </p>
                </ContentStack>
                {tab !== "delivery" && (
                  <Label className="field block">
                    Time limit per command (seconds)
                    <Input
                      type="number"
                      min={1}
                      max={900}
                      required
                      value={value.timeout}
                      onChange={(e) =>
                        setDrafts({
                          ...drafts,
                          [tab]: { ...value, timeout: Number(e.target.value) },
                        })
                      }
                    />
                  </Label>
                )}
                {stale && (
                  <ContentStack>
                    <p>These settings changed. Your draft is preserved.</p>
                    <ConfirmButton
                      size="sm"
                      variant="outline"
                      binding={JSON.stringify([
                        tab,
                        value,
                        resource.data?.revision,
                      ])}
                      title="Discard this tab’s draft?"
                      description="Load the current saved settings. Other tabs keep their drafts."
                      action={() =>
                        setDrafts((current) => {
                          const next = { ...current };
                          delete next[tab];
                          return next;
                        })
                      }
                    >
                      Load current settings
                    </ConfirmButton>
                  </ContentStack>
                )}
                <div className="actions editor-actions">
                  <Button
                    size="sm"
                    disabled={
                      !changed || (tab === "delivery" && !value.text.trim())
                    }
                  >
                    {busy === tab ? "Saving…" : "Save changes"}
                  </Button>
                  {tab !== "delivery" && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={
                        changed ||
                        prerequisiteDirty ||
                        setup.busy ||
                        validation.busy ||
                        check.data?.status === "uncertain"
                      }
                      onClick={() => void check.validate()}
                    >
                      Validate
                    </Button>
                  )}
                </div>
                {tab !== "delivery" && prerequisiteDirty && (
                  <p className="detail-metadata">
                    Save the Delivery{tab === "validation" ? " and Setup" : ""}{" "}
                    changes before validating.
                  </p>
                )}
              </fieldset>
            </form>
            {tab !== "delivery" && (
              <ValidationResult scope={tab} resource={check} />
            )}
          </div>
        );
      })}
      <div
        role="tabpanel"
        id={`${panelId}-execution`}
        aria-labelledby={`${panelId}-execution-tab`}
        hidden={active !== "execution"}
        className="content-stack"
        data-space="section"
      >
        <ResourceRetry resources={[execution]} />
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy("execution");
            try {
              await execution.save();
              toast.success("Changes saved.");
            } catch (error) {
              reportError(error, "Could not save changes");
            } finally {
              setBusy(null);
            }
          }}
        >
          <fieldset
            disabled={!!busy || !execution.loaded}
            className="content-stack"
            data-space="section"
          >
            <ContentStack space="tight">
              <Label className="field block">
                Result execution
                <NativeSelect
                  aria-describedby={`${id}-execution-help`}
                  value={execution.noPreview ? "none" : "command"}
                  onChange={(e) =>
                    execution.chooseNoPreview(e.target.value === "none")
                  }
                >
                  <option value="command">Run a command</option>
                  <option value="none">No runnable result</option>
                </NativeSelect>
              </Label>
              <p id={`${id}-execution-help`} className="detail-metadata">
                Defaults for new results. Workers can provide a result-specific
                recipe.
              </p>
            </ContentStack>
            {execution.noPreview ? (
              <Label className="field block">
                Reason
                <Textarea
                  aria-label="Reason"
                  rows={2}
                  value={execution.reason}
                  onChange={(e) => execution.changeReason(e.target.value)}
                />
              </Label>
            ) : (
              <>
                <ContentStack space="tight">
                  <Label className="field block">
                    Run command
                    <Textarea
                      aria-label="Run command"
                      aria-describedby={`${id}-run-help`}
                      rows={3}
                      value={execution.command}
                      onChange={(e) => execution.change(e.target.value)}
                    />
                  </Label>
                  <p id={`${id}-run-help`} className="detail-metadata">
                    Runs after setup. Keep services in the foreground; batch
                    commands should finish.
                  </p>
                </ContentStack>
                <SettingsCheckbox
                  checked={execution.autoPrepare}
                  hint="Prepare new reviewed results while Flowfield is open. This does not start the application."
                  onChange={(e) =>
                    execution.changeRuntime({ autoPrepare: e.target.checked })
                  }
                >
                  Prepare new results automatically
                </SettingsCheckbox>
                <ContentStack space="tight">
                  <Label className="field block">
                    Readiness command
                    <Textarea
                      aria-label="Readiness command"
                      aria-describedby={`${id}-ready-help`}
                      rows={3}
                      value={execution.readyCommand}
                      onChange={(e) =>
                        execution.changeRuntime({
                          readyCommand: e.target.value,
                        })
                      }
                    />
                  </Label>
                  <p id={`${id}-ready-help`} className="detail-metadata">
                    For services, a command that succeeds when usable. Leave
                    empty for a batch command.
                  </p>
                </ContentStack>
                <ContentStack space="tight">
                  <Label className="field block">
                    Execution check time limit (seconds)
                    <Input
                      type="number"
                      min={1}
                      max={900}
                      required
                      aria-describedby={`${id}-timeout-help`}
                      value={execution.runTimeout}
                      onChange={(e) =>
                        execution.changeRuntime({
                          runTimeout: Number(e.target.value),
                        })
                      }
                    />
                  </Label>
                  <p id={`${id}-timeout-help`} className="detail-metadata">
                    How long verification waits for readiness or batch
                    completion. Running services can continue afterward.
                  </p>
                </ContentStack>
              </>
            )}
            {execution.stale && (
              <ContentStack>
                <p>Execution settings changed. Your draft is preserved.</p>
                <ConfirmButton
                  size="sm"
                  variant="outline"
                  binding={execution.binding}
                  title="Discard this tab’s draft?"
                  description="Load the current execution defaults. Other tabs keep their drafts."
                  action={() => execution.loadCurrent()}
                >
                  Load current settings
                </ConfirmButton>
              </ContentStack>
            )}
            <div className="actions editor-actions">
              <Button size="sm" disabled={!execution.dirty || !execution.valid}>
                {busy === "execution" ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </fieldset>
        </form>
      </div>
    </>
  );
}
