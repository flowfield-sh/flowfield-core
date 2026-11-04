import { Children, Fragment, type ReactNode } from "react";
import { Separator } from "@/components/ui/separator";
import { CircleCheck, CircleAlert, Info, LoaderCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { WorkState } from "./WorkState";
import type { ComponentProps } from "react";
import { ContentStack } from "./DetailLayout";

/** Persistent state, its explanation, and its next actions share one visual owner. */
export function StatusNotice({
  title,
  tone = "neutral",
  children,
  actions,
  state,
}: {
  title: string;
  state?: ComponentProps<typeof WorkState>["state"];
  tone?: "neutral" | "success" | "attention" | "progress";
  children?: ReactNode;
  actions?: ReactNode;
}) {
  const Icon = {
    neutral: Info,
    success: CircleCheck,
    attention: CircleAlert,
    progress: LoaderCircle,
  }[tone];
  return (
    <Alert role="status" className="status-notice" data-tone={tone}>
      {!state && <Icon aria-hidden="true" />}
      <AlertTitle role="heading" aria-level={3}>
        {state ? <WorkState state={state} /> : title}
      </AlertTitle>
      {(children || actions) && (
        <AlertDescription>
          <ContentStack className="notice-content w-full">
            {children}
            {actions && <div className="actions">{actions}</div>}
          </ContentStack>
        </AlertDescription>
      )}
    </Alert>
  );
}

/** Separate independent items inside a notice, keeping each item’s actions with it. */
export function StatusNoticeItems({ children }: { children: ReactNode }) {
  const items = Children.toArray(children);
  if (!items.length) return null;
  return (
    <ContentStack className="w-full">
      {items.map((item, index) => (
        <Fragment key={index}>
          {index > 0 && <Separator className="my-2" />}
          {item}
        </Fragment>
      ))}
    </ContentStack>
  );
}
