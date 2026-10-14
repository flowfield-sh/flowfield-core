import { ContentStack, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";

/** The same question hierarchy wherever a human is asked to answer. */
export function QuestionPrompt({
  question,
  context,
  recommendation,
}: {
  question: string;
  context: string;
  recommendation?: string;
}) {
  return (
    <ContentStack space="section" className="question-prompt">
      <h3 className="detail-title">{question}</h3>
      {(context || recommendation) && (
        <Disclosure summary="Context">
          <ContentStack>
            {context && <Markdown>{context}</Markdown>}
            {recommendation && (
              <ContentStack space="tight">
                <strong>Suggested approach</strong>
                <Markdown>{recommendation}</Markdown>
              </ContentStack>
            )}
          </ContentStack>
        </Disclosure>
      )}
    </ContentStack>
  );
}
