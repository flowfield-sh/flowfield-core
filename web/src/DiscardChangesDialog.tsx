import { useRef, useState, type ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";

/** Navigation blockers keep their Escape guard; local decisions can allow dismissal. */
export function DiscardChangesDialog({
  open,
  cancel,
  discard,
  title = "Discard your unsaved edits?",
  description = "Your changes have not been saved.",
  discardLabel = "Discard changes",
  allowEscape = false,
  focusKey,
}: {
  open: boolean;
  cancel: () => void;
  discard: () => void;
  title?: string;
  description?: ReactNode;
  discardLabel?: string;
  allowEscape?: boolean;
  focusKey?: string;
}) {
  const keep = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const focusScope = useRef(focusKey);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) cancel();
      }}
    >
      <DialogContent
        role="alertdialog"
        showCloseButton={false}
        onEscapeKeyDown={(event) => {
          if (!allowEscape) event.preventDefault();
        }}
        onInteractOutside={(event) => event.preventDefault()}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          focusScope.current = focusKey;
          previousFocus.current = document.activeElement as HTMLElement;
          keep.current?.focus();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (
            focusScope.current === focusKey &&
            previousFocus.current?.isConnected
          )
            previousFocus.current.focus({ preventScroll: true });
        }}
      >
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
        <div className="actions">
          <Button type="button" ref={keep} variant="outline" onClick={cancel}>
            Keep editing
          </Button>
          <Button type="button" onClick={discard}>
            {discardLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** A local discard decision expires when its route, record, revision or draft changes. */
export function useDiscardChanges(binding: string, disabled: boolean) {
  const location = useLocation();
  const scope = JSON.stringify([location.key, binding]);
  const [pending, setPending] = useState<{
    scope: string;
    action: () => void;
    reload: boolean;
  } | null>(null);
  if (pending && (pending.scope !== scope || disabled)) setPending(null);
  return {
    request(action: () => void, reload = false) {
      if (!disabled) setPending({ scope, action, reload });
    },
    dialog: (
      <DiscardChangesDialog
        open={!!pending}
        allowEscape
        focusKey={scope}
        title={pending?.reload ? "Discard edits and load latest?" : undefined}
        description={
          pending?.reload
            ? "The latest saved version will replace your unsaved edits."
            : undefined
        }
        discardLabel={pending?.reload ? "Load latest" : undefined}
        cancel={() => setPending(null)}
        discard={() => {
          const selected = pending;
          setPending(null);
          if (selected?.scope === scope && !disabled) selected.action();
        }}
      />
    ),
  };
}
