import { RuntimeVerification } from "./RuntimeVerification";
import { CheckEvidence } from "./CheckEvidence";
export { CheckEvidence } from "./CheckEvidence";
import { ConfirmButton } from "./ConfirmButton";
import { ValidationProgress, type IntegrationRead } from "./ValidationProgress";
import { ProjectCheckPermission } from "./ProjectCheckPermission";
import { StatusNotice, StatusNoticeItems } from "./StatusNotice";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { lazy, Suspense, useEffect, useId, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useResource } from "./useResource";
import { request, label } from "./workspace";
import { useInspectionSettings } from "./useInspectionSettings";
import { SettingsCheckbox } from "./SettingsCheckbox";
import type { components } from "./api-schema";
import type { DiffViewState } from "./CodeDiff";
const CodeDiff = lazy(() => import("./CodeDiff"));
type Settings = components["schemas"]["IntegrationSettings"];
type Record = IntegrationRead;
const lines = (text: string) =>
  text
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);
export function FailureEvidence({ problem }: { problem: string }) {
  const [summary, ...details] = problem.split("\n");
  return (
    <>
      <Alert variant="destructive">
        <AlertDescription>{summary}</AlertDescription>
      </Alert>
      {!!details.length && (
        <Disclosure summary={<>Failure details</>}>
          <pre className="evidence-output">{details.join("\n")}</pre>
        </Disclosure>
      )}
    </>
  );
}

export function IntegrationSettings({
  projectId,
  onDirty,
  refresh,
}: {
  projectId: string;
  onDirty: (dirty: boolean) => void;
  refresh: unknown;
}) {
  const id = useId();
  const path = `projects/${projectId}/integration`;
  const resource = useResource<Settings>(path, refresh);
  const [draft, setDraft] = useState<{
    target: string;
    checks: string;
    setup: string;
    setupTimeout: number;
    checkTimeout: number;
    revision: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const inspection = useInspectionSettings(projectId, refresh);
  const target = draft?.target ?? resource.data?.target_branch ?? "";
  const checks = draft?.checks ?? resource.data?.checks.join("\n") ?? "";
  const setup = draft?.setup ?? resource.data?.setup_commands.join("\n") ?? "";
  const setupTimeout =
    draft?.setupTimeout ?? resource.data?.setup_timeout_seconds ?? 120;
  const checkTimeout =
    draft?.checkTimeout ?? resource.data?.check_timeout_seconds ?? 60;
  const fields = {
    revision: draft?.revision ?? resource.data?.revision ?? 1,
    target,
    checks,
    setup,
    setupTimeout,
    checkTimeout,
  };
  const dirty =
    target !== (resource.data?.target_branch ?? "") ||
    checks !== (resource.data?.checks.join("\n") ?? "") ||
    setup !== (resource.data?.setup_commands.join("\n") ?? "") ||
    setupTimeout !== (resource.data?.setup_timeout_seconds ?? 120) ||
    checkTimeout !== (resource.data?.check_timeout_seconds ?? 60);
  useEffect(() => {
    onDirty(dirty || inspection.dirty);
    return () => onDirty(false);
  }, [dirty, inspection.dirty, onDirty]);
  return (
    <section
      aria-label="Integration settings"
      className="worker-settings content-stack"
      data-space="settings"
    >
      <ResourceRetry resources={[resource, inspection]} />
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!resource.data) return;
          setBusy(true);
          let integrationSaved = false;
          let savingRun = false;
          try {
            if (dirty) {
              const value = await request<Settings>(path, "PUT", {
                expected_revision: fields.revision,
                target_branch: target,
                checks: lines(checks),
                setup_commands: setup
                  .split("\n")
                  .map((v) => v.trim())
                  .filter(Boolean),
                setup_timeout_seconds: setupTimeout,
                check_timeout_seconds: checkTimeout,
              });
              resource.invalidate();
              resource.setData(value);
              setDraft(null);
              integrationSaved = true;
            }
            savingRun = true;
            await inspection.save();
            toast.success("Integration settings saved.");
          } catch (e) {
            reportError(
              e,
              integrationSaved
                ? "Integration saved; run command save was not confirmed"
                : savingRun
                  ? "Could not save run command"
                  : "Could not save integration settings",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset
          disabled={busy || !resource.data}
          className="content-stack"
          data-space="settings"
        >
          <DetailSection settings title="Delivery">
            <ContentStack space="tight">
              <Label className="field block">
                Destination branch
                <Input
                  aria-label="Destination branch"
                  aria-describedby={`${id}-destination-help`}
                  required
                  value={target}
                  onChange={(e) =>
                    setDraft({ ...fields, target: e.target.value })
                  }
                />
              </Label>
              <p id={`${id}-destination-help`} className="detail-metadata">
                Approved code updates this local branch. Flowfield does not push
                or deploy it.
              </p>
            </ContentStack>
          </DetailSection>
          <DetailSection settings title="Preparation and checks">
            <ContentStack space="section">
              <ContentStack space="tight">
                <Label className="field block">
                  Setup commands
                  <Textarea
                    aria-label="Setup commands"
                    aria-describedby={`${id}-setup-help`}
                    rows={3}
                    value={setup}
                    onChange={(e) =>
                      setDraft({ ...fields, setup: e.target.value })
                    }
                  />
                </Label>
                <p id={`${id}-setup-help`} className="detail-metadata">
                  Install dependencies and build each work copy. One command per
                  line; keep source files unchanged.
                </p>
              </ContentStack>
              <ContentStack space="tight">
                <Label className="field block">
                  Validation commands
                  <Textarea
                    aria-label="Validation commands"
                    aria-describedby={`${id}-validation-help`}
                    rows={3}
                    value={checks}
                    placeholder="For example: npm test"
                    onChange={(e) =>
                      setDraft({ ...fields, checks: e.target.value })
                    }
                  />
                </Label>
                <p id={`${id}-validation-help`} className="detail-metadata">
                  Optional tests and checks to pass before code is ready for
                  review. One command per line.
                </p>
              </ContentStack>
              <Disclosure summary="Time limits">
                <ContentStack space="section">
                  <Label className="field block">
                    Seconds per setup command
                    <Input
                      type="number"
                      min={1}
                      max={900}
                      value={setupTimeout}
                      onChange={(e) =>
                        setDraft({
                          ...fields,
                          setupTimeout: Number(e.target.value),
                        })
                      }
                    />
                  </Label>
                  <Label className="field block">
                    Seconds per validation or result check
                    <Input
                      type="number"
                      min={1}
                      max={900}
                      value={checkTimeout}
                      onChange={(e) =>
                        setDraft({
                          ...fields,
                          checkTimeout: Number(e.target.value),
                        })
                      }
                    />
                  </Label>
                </ContentStack>
              </Disclosure>
            </ContentStack>
          </DetailSection>
          <DetailSection settings title="Run results">
            <ContentStack space="section">
              <ContentStack space="tight">
                <Label className="field block">
                  Result preview
                  <NativeSelect
                    value={inspection.noPreview ? "none" : "command"}
                    disabled={!inspection.loaded}
                    onChange={(event) =>
                      inspection.chooseNoPreview(event.target.value === "none")
                    }
                  >
                    <option value="command">Run a command</option>
                    <option value="none">No runnable result</option>
                  </NativeSelect>
                </Label>
                <p className="detail-metadata">
                  Defaults for new results. Each result keeps the recipe it was
                  verified with.
                </p>
              </ContentStack>
              {inspection.noPreview ? (
                <Label className="field block">
                  Reason
                  <Textarea
                    aria-label="No preview reason"
                    rows={2}
                    value={inspection.reason}
                    placeholder="For example: a library verified through its tests"
                    onChange={(event) =>
                      inspection.changeReason(event.target.value)
                    }
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
                        rows={2}
                        value={inspection.command}
                        disabled={!inspection.loaded}
                        placeholder="For example: npm run dev"
                        onChange={(event) =>
                          inspection.change(event.target.value)
                        }
                      />
                    </Label>
                    <p id={`${id}-run-help`} className="detail-metadata">
                      Runs after setup. Keep services in the foreground; batch
                      commands should finish.
                    </p>
                  </ContentStack>
                  <SettingsCheckbox
                    checked={inspection.autoPrepare}
                    disabled={!inspection.loaded || busy}
                    hint="Keep new reviewed results ready to run while Flowfield is open. Preparation does not start the result."
                    onChange={(event) =>
                      inspection.changeRuntime({
                        autoPrepare: event.target.checked,
                      })
                    }
                  >
                    Prepare new results automatically
                  </SettingsCheckbox>
                  <Disclosure summary="Service and output options">
                    <ContentStack space="section">
                      <ContentStack space="tight">
                        <Label className="field block">
                          Readiness command
                          <Textarea
                            aria-label="Readiness command"
                            aria-describedby={`${id}-readiness-help`}
                            rows={2}
                            value={inspection.readyCommand}
                            onChange={(event) =>
                              inspection.changeRuntime({
                                readyCommand: event.target.value,
                              })
                            }
                          />
                        </Label>
                        <p
                          id={`${id}-readiness-help`}
                          className="detail-metadata"
                        >
                          For a long-running service, a command that exits
                          successfully when it is usable. Leave empty for a
                          batch command.
                        </p>
                      </ContentStack>
                      <ContentStack space="tight">
                        <Label className="field block">
                          Result URL
                          <Input
                            aria-label="Result URL"
                            aria-describedby={`${id}-url-help`}
                            value={inspection.url}
                            onChange={(event) =>
                              inspection.changeRuntime({
                                url: event.target.value,
                              })
                            }
                            placeholder="http://127.0.0.1:${FLOWFIELD_PORT}"
                          />
                        </Label>
                        <p id={`${id}-url-help`} className="detail-metadata">
                          Optional. Use {"${FLOWFIELD_PORT}"} in the URL and
                          $FLOWFIELD_PORT in commands for a separate local port.
                        </p>
                      </ContentStack>
                      <ContentStack space="tight">
                        <Label className="field block">
                          Generated files
                          <Textarea
                            aria-label="Generated files"
                            aria-describedby={`${id}-outputs-help`}
                            rows={2}
                            value={inspection.outputPaths}
                            onChange={(event) =>
                              inspection.changeRuntime({
                                outputPaths: event.target.value,
                              })
                            }
                            placeholder="report.json"
                          />
                        </Label>
                        <p
                          id={`${id}-outputs-help`}
                          className="detail-metadata"
                        >
                          Optional file paths inside FLOWFIELD_OUTPUT_DIR, one
                          per line. Completed runs retain these files for
                          preview and download.
                        </p>
                      </ContentStack>
                    </ContentStack>
                  </Disclosure>
                </>
              )}
              {inspection.stale && (
                <ContentStack>
                  <p>Run settings changed. Your draft is preserved.</p>
                  <ConfirmButton
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    binding={inspection.binding}
                    title="Discard your run settings draft?"
                    description="Load the current saved defaults in place of your draft."
                    action={() => inspection.loadCurrent()}
                  >
                    Load current run settings
                  </ConfirmButton>
                </ContentStack>
              )}
            </ContentStack>
          </DetailSection>
          {draft &&
            resource.data &&
            draft.revision !== resource.data.revision && (
              <ContentStack>
                <p>
                  Preparation or delivery settings changed. Your draft is
                  preserved.
                </p>
                <ConfirmButton
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  binding={JSON.stringify([draft, resource.data.revision])}
                  title="Discard your preparation and delivery draft?"
                  description="Load the current saved settings in place of your draft. Your run settings draft will be kept."
                  action={() => setDraft(null)}
                >
                  Load current preparation and delivery settings
                </ConfirmButton>
              </ContentStack>
            )}
          <Button
            size="sm"
            disabled={
              (!dirty && !inspection.dirty) ||
              !target ||
              !inspection.loaded ||
              !inspection.valid
            }
          >
            {busy ? "Saving…" : "Save integration settings"}
          </Button>
        </fieldset>
      </form>
      {resource.data && (
        <ProjectCheckPermission
          projectId={projectId}
          revision={resource.data.revision}
          disabled={dirty || inspection.dirty || busy}
          refresh={refresh}
        />
      )}
      {resource.data && (
        <SetupValidation
          projectId={projectId}
          revision={resource.data.revision}
          disabled={dirty || inspection.dirty || busy}
          refresh={refresh}
        />
      )}
    </section>
  );
}

function SetupValidation({
  projectId,
  revision,
  disabled,
  refresh,
}: {
  projectId: string;
  revision: number;
  disabled: boolean;
  refresh: unknown;
}) {
  type Check = components["schemas"]["SetupCheck"];
  const path = `projects/${projectId}/setup-validation`;
  const resource = useResource<Check>(path, `${revision}:${String(refresh)}`);
  const [busy, setBusy] = useState(false);
  const value = resource.data;
  const validateAction = (
    <Button
      size="sm"
      variant="outline"
      disabled={
        disabled ||
        busy ||
        value?.status === "checking" ||
        value?.status === "uncertain"
      }
      onClick={async () => {
        setBusy(true);
        try {
          const checked = await request<Check>(path, "POST", {
            expected_revision: revision,
          });
          resource.invalidate();
          resource.setData(checked);
          if (checked.status === "passed" && !checked.checkout_problem)
            toast.success("Setup checks passed.");
          else if (checked.status === "checking")
            toast.info("Setup validation started.");
          else if (checked.status === "failed")
            toast.error(checked.problem || "Setup checks failed.");
          else
            toast.warning("Setup validation needs attention.", {
              description:
                checked.problem || checked.checkout_problem || undefined,
            });
        } catch (e) {
          reportError(e, "Could not validate setup");
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Checking setup…" : "Validate saved setup"}
    </Button>
  );
  return (
    <DetailSection settings title="Validate setup">
      <p>
        Test saved setup and checks in a managed copy. No model call; the queue
        stays unchanged.
      </p>
      {!value && validateAction}

      <ResourceRetry resources={[resource]} />
      {value && (
        <ContentStack>
          <StatusNotice
            title={
              busy || value.status === "checking"
                ? "Validating project setup…"
                : value.stale
                  ? "Validation is out of date"
                  : value.status === "passed"
                    ? value.checks.length
                      ? "Setup and validation commands passed"
                      : "Setup validated · no validation commands configured"
                    : value.status === "failed"
                      ? "Project setup validation failed"
                      : "Project setup validation needs attention"
            }
            tone={
              busy || value.status === "checking"
                ? "progress"
                : value.stale ||
                    value.status !== "passed" ||
                    value.checkout_problem
                  ? "attention"
                  : "success"
            }
            actions={validateAction}
          >
            <StatusNoticeItems>
              <ContentStack>
                <p>
                  {value.stale
                    ? "Settings or destination changed; validate again."
                    : value.status === "passed"
                      ? "Setup and checks passed in the managed environment."
                      : value.status === "checking"
                        ? "Running the saved commands in a managed copy."
                        : value.status === "uncertain"
                          ? "Validation stopped without a confirmed outcome. Inspect the retained evidence before continuing."
                          : "Review the failure below, update the settings, then validate again."}
                </p>
                {value.problem && <p>{value.problem.split("\n")[0]}</p>}
              </ContentStack>
              {value.checkout_problem && (
                <p>
                  <strong>Checkout is not ready for delivery.</strong>{" "}
                  {value.checkout_problem}
                </p>
              )}
            </StatusNoticeItems>
          </StatusNotice>
          {value.problem?.includes("\n") && (
            <Disclosure summary="Failure details">
              <pre className="evidence-output">
                {value.problem.split("\n").slice(1).join("\n")}
              </pre>
            </Disclosure>
          )}
          <CheckEvidence
            checks={value.setup}
            title="Setup output"
            logBase={`projects/${value.project_id}/setup-validation/${value.id}/logs/setup`}
          />
          <CheckEvidence
            checks={value.checks}
            title="Validation output"
            logBase={`projects/${value.project_id}/setup-validation/${value.id}/logs/checks`}
          />
          {value.workspace && (
            <Disclosure summary={<>Validation copy</>}>
              <code>{value.workspace}</code>
            </Disclosure>
          )}
        </ContentStack>
      )}
    </DetailSection>
  );
}

export function IntegrationEvidence({
  projectId,
  record,
  view,
  changeView,
}: {
  projectId: string;
  record: Record;
  view: DiffViewState;
  changeView: (view: DiffViewState) => void;
}) {
  const [diff, setDiff] = useState(false);
  return (
    <ContentStack space="section" className="integration-evidence">
      <ValidationProgress record={record} />
      <RuntimeVerification value={record.runtime_verification} />
      <DetailSection title="Branch">
        <p>
          {record.status === "ready"
            ? record.checks.length
              ? "Checks passed · candidate prepared"
              : "Candidate prepared · no validation commands configured"
            : label(record.status)}{" "}
          · <strong>{record.target_branch}</strong>
        </p>
        <p>
          Branch commit: <code>{record.target_before.slice(0, 12)}</code>.
          {record.candidate_commit && (
            <>
              {" "}
              Checked commit:{" "}
              <code>{record.candidate_commit.slice(0, 12)}</code>.
            </>
          )}
        </p>
      </DetailSection>
      {record.problem && <FailureEvidence problem={record.problem} />}
      {record.reused_from && (
        <p>
          Reused successful checks for this unchanged candidate and
          configuration.
        </p>
      )}
      <CheckEvidence
        checks={record.setup_checks ?? []}
        title="Runtime setup"
        logBase={`workspaces/${record.workspace_id}/integrations/${record.id}/logs/setup`}
      />
      <CheckEvidence
        checks={record.checks}
        title="Validation checks"
        logBase={`workspaces/${record.workspace_id}/integrations/${record.id}/logs/checks`}
      />
      {record.candidate_commit && (
        <>
          <Button size="sm" variant="outline" onClick={() => setDiff(!diff)}>
            {diff ? "Hide combined changes" : "View combined changes"}
          </Button>
          {diff && (
            <Suspense fallback={<p>Loading changes…</p>}>
              <CodeDiff
                path={`workspaces/${projectId}/integrations/${record.id}`}
                result={record.candidate_commit}
                view={view}
                changeView={changeView}
              />
            </Suspense>
          )}
        </>
      )}
      {record.workspace && (
        <Disclosure summary={<>Validation checkout</>}>
          <code>{record.workspace}</code>
        </Disclosure>
      )}
    </ContentStack>
  );
}
