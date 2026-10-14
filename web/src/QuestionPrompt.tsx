import { ContentStack } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";

/** The same question hierarchy wherever a human is asked to answer. */
export function QuestionPrompt({
  question,
  context,
  recommendation,
  verificationUrl,
}: {
  question: string;
  context: string;
  recommendation?: string;
  verificationUrl?: string | null;
}) {
  return (
    <ContentStack space="section" className="question-prompt">
      <h3 className="detail-title">{question}</h3>
      {(context || recommendation) && (
        <ContentStack>
          {context && <Markdown>{context}</Markdown>}
          {recommendation && (
            <ContentStack space="tight">
              <strong>Suggested approach</strong>
              <Markdown>{recommendation}</Markdown>
            </ContentStack>
          )}
        </ContentStack>
      )}
      {verificationUrl && (
        <p>
          <WorkspaceLink to={verificationUrl}>
            View milestone verification
          </WorkspaceLink>
        </p>
      )}
    </ContentStack>
  );
}
