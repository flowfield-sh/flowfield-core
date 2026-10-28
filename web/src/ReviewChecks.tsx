import { ContentStack, Disclosure } from "./DetailLayout";
import { ValidationProgress, type IntegrationRead } from "./ValidationProgress";
import { CheckEvidence } from "./CheckEvidence";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { label } from "./workspace";

type Integration = IntegrationRead;

export function ReviewChecks({
  preparation,
  reusePending,
  availability,
  report,
  completion,
  branch,
  historyHref,
}: {
  preparation: Integration | null;
  reusePending?: boolean;
  availability: Integration | null;
  report: string;
  completion: string;
  branch: string | null;
  historyHref: string;
}) {
  return (
    <ContentStack className="review-checks">
      {preparation?.progress && <ValidationProgress record={preparation} />}
      {availability?.progress && <ValidationProgress record={availability} />}
      {availability && (
        <Disclosure
          role="region"
          aria-label="Current branch checks"
          open={availability.status !== "integrated"}
          summary={
            <>
              Destination checks:{" "}
              {availability.status === "integrated"
                ? reusePending
                  ? "Previously passed; current validation needed"
                  : availability.checks.length
                    ? "Passed"
                    : "No validation commands configured"
                : label(availability.status)}
            </>
          }
        >
          <p>Recorded checks on {branch} after delivery.</p>
          {availability.problem && <p>{availability.problem}</p>}
          <CheckEvidence
            checks={availability.setup_checks ?? []}
            logBase={`workspaces/${availability.workspace_id}/integrations/${availability.id}/logs/setup`}
            title="Setup commands"
          />
          <CheckEvidence
            checks={availability.checks}
            logBase={`workspaces/${availability.workspace_id}/integrations/${availability.id}/logs/checks`}
            title="Project commands"
          />
          <WorkspaceLink to={historyHref + "/validation:" + availability.id}>
            Open branch check details
          </WorkspaceLink>
        </Disclosure>
      )}
      {preparation && !preparation.progress ? (
        <Disclosure
          summary={
            <>
              {preparation.status === "integrated"
                ? "Delivery checks: "
                : "Flowfield: "}
              {preparation.checks.length
                ? `${preparation.checks.filter((check) => check.exit_code === 0).length} of ${preparation.checks.length} project checks passed`
                : ["ready", "integrated"].includes(preparation.status)
                  ? "No validation commands configured"
                  : "project checks not run yet"}
            </>
          }
          className="review-disclosure check-source"
          help={
            preparation.status === "integrated"
              ? "Checks recorded for the successful delivery. Current reuse validation is separate. Expand a command for its output."
              : `Independent checks of the code combined with ${branch}. Expand a command for its output.`
          }
          open={[
            ...(preparation.setup_checks ?? []),
            ...preparation.checks,
          ].some((check) => check.exit_code !== 0)}
        >
          {preparation.reused_from && (
            <p>
              Reused successful checks for this unchanged candidate and
              configuration.
            </p>
          )}
          <CheckEvidence
            checks={preparation.setup_checks ?? []}
            logBase={`workspaces/${preparation.workspace_id}/integrations/${preparation.id}/logs/setup`}
            title="Setup commands"
          />
          <CheckEvidence
            checks={preparation.checks}
            title="Project commands"
            logBase={`workspaces/${preparation.workspace_id}/integrations/${preparation.id}/logs/checks`}
          />
        </Disclosure>
      ) : (
        !preparation &&
        completion === "code" && (
          <p className="muted">
            Flowfield has not prepared branch checks for these changes yet.
          </p>
        )
      )}
      <Disclosure
        summary={<>Worker’s test report</>}
        className="review-disclosure check-source"
        help="The worker’s account of testing in its own checkout; this is separate from Flowfield’s observed checks."
      >
        <Markdown>{report}</Markdown>
      </Disclosure>
    </ContentStack>
  );
}
