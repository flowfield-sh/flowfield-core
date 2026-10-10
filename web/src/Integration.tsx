import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { lazy, Suspense, useEffect, useId, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useResource } from "./useResource";
import { request, label } from "./workspace";
import { useInspectionSettings } from "./useInspectionSettings";
import type { components } from "./api-schema";
import type { DiffViewState } from "./CodeDiff";
const CodeDiff = lazy(() => import("./CodeDiff"));
type Settings = components["schemas"]["IntegrationSettings"];
type Record = components["schemas"]["Integration"];
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
  const resource = useResource<Settings>(path, projectId);
  const [draft, setDraft] = useState<{
    target: string;
    checks: string;
    setup: string;
    setupTimeout: number;
    checkTimeout: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inspection = useInspectionSettings(projectId, refresh);
  const target = draft?.target ?? resource.data?.target_branch ?? "";
  const checks = draft?.checks ?? resource.data?.checks.join("\n") ?? "";
  const setup = draft?.setup ?? resource.data?.setup_commands.join("\n") ?? "";
  const setupTimeout =
    draft?.setupTimeout ?? resource.data?.setup_timeout_seconds ?? 120;
  const checkTimeout =
    draft?.checkTimeout ?? resource.data?.check_timeout_seconds ?? 60;
  const fields = {
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
      data-space="section"
    >
      <p>Worker setup, checks and local code delivery.</p>
      {(error || resource.error || inspection.error) && (
        <Alert variant="destructive">
          <AlertDescription>
            {error || resource.error || inspection.error}
          </AlertDescription>
        </Alert>
      )}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!resource.data) return;
          setBusy(true);
          setError("");
          try {
            if (dirty) {
              const value = await request<Settings>(path, "PUT", {
                expected_revision: resource.data.revision,
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
            }
            await inspection.save();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset
          disabled={busy || !resource.data}
          className="content-stack"
          data-space="section"
        >
          <DetailSection title="Destination">
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
                Approval updates this branch and the project files. A missing
                branch starts at the current commit. No push or deploy.
              </p>
            </ContentStack>
          </DetailSection>
          <DetailSection title="Validation">
            <ContentStack space="tight">
              <Label className="field block">
                Validation commands
                <Textarea
                  aria-label="Validation commands"
                  aria-describedby={`${id}-validation-help`}
                  required
                  rows={3}
                  value={checks}
                  placeholder="Your project’s repeatable check command"
                  onChange={(e) =>
                    setDraft({ ...fields, checks: e.target.value })
                  }
                />
              </Label>
              <p id={`${id}-validation-help`} className="detail-metadata">
                Required before approval. One shell command per line.
              </p>
            </ContentStack>
          </DetailSection>
          <DetailSection title="Runtime setup">
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
                Install dependencies in each work copy. One command per line;
                keep source files unchanged.
              </p>
            </ContentStack>
          </DetailSection>
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
                onChange={(event) => inspection.change(event.target.value)}
              />
            </Label>
            <p id={`${id}-run-help`} className="detail-metadata">
              Used by Try result after setup; you run it in the prepared copy.
            </p>
            {inspection.stale && (
              <p>
                Run settings changed. Your draft is preserved.{" "}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (
                      window.confirm(
                        "Discard your run command draft and load current settings?",
                      )
                    ) {
                      inspection.loadCurrent();
                      setError("");
                    }
                  }}
                >
                  Load current run command
                </Button>
              </p>
            )}
          </ContentStack>
          <DetailSection title="Time limits">
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
                Seconds per validation command
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
          </DetailSection>
          <Button
            size="sm"
            disabled={
              (!dirty && !inspection.dirty) ||
              !target ||
              !checks.trim() ||
              !inspection.loaded
            }
          >
            Save integration settings
          </Button>
        </fieldset>
      </form>
      {resource.data && (
        <SetupValidation
          projectId={projectId}
          revision={resource.data.revision}
          disabled={dirty || busy}
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
  const [error, setError] = useState("");
  const value = resource.data;
  return (
    <DetailSection title="Validate setup">
      <p>
        Test saved setup and checks in a managed copy. No model call; the queue
        stays unchanged.
      </p>
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
          setError("");
          try {
            const checked = await request<Check>(path, "POST", {
              expected_revision: revision,
            });
            resource.invalidate();
            resource.setData(checked);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Checking setup…" : "Validate saved setup"}
      </Button>
      {(error || resource.error) && (
        <Alert variant="destructive">
          <AlertDescription>{error || resource.error}</AlertDescription>
        </Alert>
      )}
      {value && (
        <ContentStack>
          <p>
            {value.stale
              ? "Settings or destination changed; validate again."
              : value.status === "passed"
                ? "Setup and checks passed in the managed environment."
                : label(value.status)}
          </p>
          {value.problem && <FailureEvidence problem={value.problem} />}
          {value.checkout_problem && (
            <Alert variant="destructive">
              <AlertDescription>
                <strong>Checkout is not ready for delivery.</strong>{" "}
                {value.checkout_problem}
              </AlertDescription>
            </Alert>
          )}
          <CheckEvidence checks={value.setup} title="Setup output" />
          <CheckEvidence checks={value.checks} title="Validation output" />
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
      <DetailSection title="Branch">
        <p>
          {record.status === "ready"
            ? "Checks passed · candidate prepared"
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
      <CheckEvidence checks={record.setup_checks ?? []} title="Runtime setup" />
      <CheckEvidence checks={record.checks} title="Validation checks" />
      {record.candidate_commit && (
        <>
          <Button size="sm" variant="outline" onClick={() => setDiff(!diff)}>
            {diff ? "Hide combined changes" : "View combined changes"}
          </Button>
          {diff && (
            <Suspense fallback={<p>Loading changes…</p>}>
              <CodeDiff
                path={`projects/${projectId}/integrations/${record.id}`}
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

export function CheckEvidence({
  checks,
  title,
}: {
  checks: components["schemas"]["CheckResult"][];
  title: string;
}) {
  if (!checks.length) return null;
  return (
    <ContentStack space="flush">
      <strong>{title}</strong>
      <ContentStack space="flush">
        {checks.map((check, index) => (
          <Disclosure
            summary={
              <>
                {check.exit_code === 0
                  ? "Passed"
                  : `Failed (${check.exit_code})`}{" "}
                · <code>{check.command}</code>
              </>
            }
            key={index}
            open={check.exit_code !== 0}
          >
            <pre className="evidence-output">
              {check.output.replace(/^(?:[ \t]*\r?\n)+/, "") || "No output."}
              {check.truncated && "\nOutput truncated."}
            </pre>
          </Disclosure>
        ))}
      </ContentStack>
    </ContentStack>
  );
}
