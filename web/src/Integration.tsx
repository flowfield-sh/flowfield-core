import { RuntimeVerification } from "./RuntimeVerification";
import { CheckEvidence } from "./CheckEvidence";
export { CheckEvidence } from "./CheckEvidence";
import { ValidationProgress, type IntegrationRead } from "./ValidationProgress";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { lazy, Suspense, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { label } from "./workspace";
import type { DiffViewState } from "./CodeDiff";
const CodeDiff = lazy(() => import("./CodeDiff"));
type Record = IntegrationRead;
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
