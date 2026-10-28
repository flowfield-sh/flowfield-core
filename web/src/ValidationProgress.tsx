import { ContentStack, Disclosure } from "./DetailLayout";
import { CheckEvidence } from "./CheckEvidence";
import type { components } from "./api-schema";

export type IntegrationRead = components["schemas"]["IntegrationRead"];
export const checking = (value: IntegrationRead) =>
  value.status === "preparing";

export function ValidationProgress({ record }: { record: IntegrationRead }) {
  const progress = record.progress;
  if (!progress) return null;
  const command = progress.current;
  const phase = {
    preparing: "Preparing candidate",
    setup: "Running setup",
    checks: "Running project checks",
    verifying: "Verifying candidate integrity",
  }[progress.phase];
  const quiet = command?.last_output_at
    ? Math.max(
        0,
        Math.floor(
          (Date.parse(command.started_at) +
            command.elapsed_seconds * 1000 -
            Date.parse(command.last_output_at)) /
            1000,
        ),
      )
    : null;
  return (
    <ContentStack role="region" aria-label="Validation progress">
      <p role="status">
        {phase}
        {command && ` · ${command.index} of ${command.total}`}
      </p>
      {command && (
        <ContentStack>
          <code className="break-all text-xs">{command.command}</code>
          <p className="text-xs text-muted-foreground">
            {Math.floor(command.elapsed_seconds)}s elapsed ·{" "}
            {command.timeout_seconds}s limit
            {quiet === null
              ? " · No output yet"
              : ` · Last output ${quiet}s ago`}
          </p>
          <Disclosure summary={<>Live output</>} open>
            {command.output ? (
              <pre className="evidence-output max-h-64 overflow-auto">
                {command.output}
              </pre>
            ) : (
              <p className="text-muted-foreground">
                Waiting for command output…
              </p>
            )}
            {command.truncated && (
              <p className="text-xs text-muted-foreground">
                Showing the most recent output.
              </p>
            )}
          </Disclosure>
        </ContentStack>
      )}
      <CheckEvidence
        checks={progress.setup_checks}
        logBase={`workspaces/${record.workspace_id}/integrations/${record.id}/logs/setup`}
        title="Completed setup commands"
      />
      <CheckEvidence
        checks={progress.checks}
        logBase={`workspaces/${record.workspace_id}/integrations/${record.id}/logs/checks`}
        title="Completed project commands"
      />
    </ContentStack>
  );
}
