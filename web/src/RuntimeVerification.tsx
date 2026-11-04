import { ContentStack, Disclosure } from "./DetailLayout";
import type { components } from "./api-schema";
export function RuntimeVerification({
  value,
}: {
  value: components["schemas"]["RuntimeVerification"] | null;
}) {
  return (
    <>
      {value && (
        <Disclosure
          summary={
            value.passed
              ? "Result verification passed"
              : "Result verification failed"
          }
        >
          {value.problem && <p>{value.problem}</p>}
          <p className="detail-metadata">
            Verified the saved recipe on this candidate in a separate copy.{" "}
            {value.cleanup_confirmed
              ? "Process cleanup confirmed."
              : "Process cleanup is unconfirmed."}
          </p>
          {value.steps.map((step, index) => (
            <ContentStack key={index}>
              <p>
                {step.name}: {step.passed ? "Passed" : "Failed"}
              </p>
              {step.output && (
                <Disclosure summary="Output excerpt">
                  <pre className="evidence-output">{step.output}</pre>
                </Disclosure>
              )}
            </ContentStack>
          ))}
        </Disclosure>
      )}
    </>
  );
}
