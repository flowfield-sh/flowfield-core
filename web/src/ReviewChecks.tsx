import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import type { components } from "./api-schema";
import { CheckEvidence } from "./Integration";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { label } from "./workspace";

type Integration = components["schemas"]["Integration"];

export function ReviewChecks({
  preparation,
  availability,
  report,
  completion,
  branch,
  historyHref,
}: {
  preparation: Integration | null;
  availability: Integration | null;
  report: string;
  completion: string;
  branch: string | null;
  historyHref: string;
}) {
  return (
    <ContentStack space="flush" className="review-checks">
      {availability && (
        <DetailSection
          aria-label="Current branch checks"
          title={
            <>
              Current branch:{" "}
              {availability.status === "integrated"
                ? "Passed"
                : label(availability.status)}
            </>
          }
        >
          <p className="muted">
            Checks on {branch} after delivery. These establish whether dependent
            work can use the code now.
          </p>
          {availability.problem && <p>{availability.problem}</p>}
          <CheckEvidence
            checks={availability.setup_checks ?? []}
            title="Setup commands"
          />
          <CheckEvidence
            checks={availability.checks}
            title="Project commands"
          />
          <WorkspaceLink to={historyHref + "/validation:" + availability.id}>
            Open branch check details
          </WorkspaceLink>
        </DetailSection>
      )}
      {preparation ? (
        <Disclosure
          summary={
            <>
              Flowfield:{" "}
              {preparation.checks.length
                ? `${preparation.checks.filter((check) => check.exit_code === 0).length} of ${preparation.checks.length} project checks passed`
                : "project checks not run yet"}
            </>
          }
          className="review-disclosure check-source"
          help={`Independent checks on the proposed code combined with ${branch}. Expand a command to read its output.`}
          open={[
            ...(preparation.setup_checks ?? []),
            ...preparation.checks,
          ].some((check) => check.exit_code !== 0)}
        >
          <CheckEvidence
            checks={preparation.setup_checks ?? []}
            title="Setup commands"
          />
          <CheckEvidence checks={preparation.checks} title="Project commands" />
        </Disclosure>
      ) : (
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
