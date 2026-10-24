import { useRef, useState, type ComponentProps, type ReactNode } from "react";
import { ActionTooltip } from "./ActionTooltip";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

/** Confirm the action selected when opened, preserving its revision-bound closure. */
export function ConfirmButton({
  title,
  description,
  action,
  children,
  actionLabel,
  requiresConfirmation = true,
  binding,
  tooltip,
  ...props
}: Omit<ComponentProps<typeof Button>, "onClick"> & {
  title: string;
  description: ReactNode;
  action: () => void;
  actionLabel?: ReactNode;
  requiresConfirmation?: boolean;
  binding?: string;
  tooltip?: string;
}) {
  const location = useLocation();
  const scope = JSON.stringify([location.key, binding]);
  const [pending, setPending] = useState<{
    action: () => void;
    scope: string;
    title: string;
    description: ReactNode;
    label: ReactNode;
  } | null>(null);
  if (pending && pending.scope !== scope) setPending(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const focusScope = useRef(scope);
  return (
    <>
      <ActionTooltip label={tooltip} disabled={props.disabled}>
        <Button
          {...props}
          ref={trigger}
          type="button"
          onClick={() => {
            if (!requiresConfirmation) action();
            else
              setPending({
                action,
                scope,
                title,
                description,
                label: actionLabel ?? children,
              });
          }}
        >
          {children}
        </Button>
      </ActionTooltip>
      <Dialog
        open={!!pending}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
      >
        <DialogContent
          role="alertdialog"
          showCloseButton={false}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            focusScope.current = scope;
            cancel.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (focusScope.current === scope && trigger.current?.isConnected)
              trigger.current.focus({ preventScroll: true });
          }}
        >
          <DialogTitle>{pending?.title ?? title}</DialogTitle>
          <DialogDescription>
            {pending?.description ?? description}
          </DialogDescription>
          <div className="actions">
            <Button
              type="button"
              disabled={props.disabled}
              onClick={() => {
                const selected = pending;
                setPending(null);
                if (selected?.scope === scope && !props.disabled)
                  selected.action();
              }}
            >
              {pending?.label ?? children}
            </Button>
            <Button
              ref={cancel}
              type="button"
              variant="outline"
              onClick={() => setPending(null)}
            >
              Cancel
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
