import { ContentStack, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";

/** The same question hierarchy wherever a human is asked to answer. */
export function QuestionPrompt({
  question,
  context,
  recommendation,
  reviewContext = false,
}: {
  question: string;
  context: string;
  recommendation?: string;
  reviewContext?: boolean;
}) {
  return (
    <ContentStack space="section" className="question-prompt">
      <h3 className="detail-title">{question}</h3>
      {(context || recommendation) && (
        <ContentStack>
          {context && !reviewContext && <Markdown>{context}</Markdown>}
          {recommendation && (
            <ContentStack space="tight">
              <strong>
                {reviewContext ? "Decision needed" : "Suggested approach"}
              </strong>
              <Markdown>{recommendation}</Markdown>
            </ContentStack>
          )}
          {context && reviewContext && (
            <Disclosure summary="Review evidence">
              <Markdown>{context}</Markdown>
            </Disclosure>
          )}
        </ContentStack>
      )}
    </ContentStack>
  );
}
