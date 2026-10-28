import { ContentStack, Disclosure } from "./DetailLayout";
import type { components } from "./api-schema";

export function CheckEvidence({
  checks,
  title,
  logBase,
}: {
  checks: components["schemas"]["CheckResult"][];
  title: string;
  logBase?: string;
}) {
  if (!checks.length) return null;
  return (
    <ContentStack>
      <strong>{title}</strong>
      <ContentStack>
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
            {check.log_id && logBase && (
              <a href={`/api/${logBase}/${index}`} download>
                {check.log_truncated
                  ? "Download retained log (size limit reached)"
                  : "Download complete log"}
              </a>
            )}
          </Disclosure>
        ))}
      </ContentStack>
    </ContentStack>
  );
}
