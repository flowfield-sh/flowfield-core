import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  checkServiceConnection,
  useServiceUnavailable,
} from "./serviceConnection";

export function ConnectionRecovery() {
  const unavailable = useServiceUnavailable();
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    const check = () => void checkServiceConnection();
    window.addEventListener("online", check);
    window.addEventListener("offline", check);
    window.addEventListener("focus", check);
    return () => {
      window.removeEventListener("online", check);
      window.removeEventListener("offline", check);
      window.removeEventListener("focus", check);
    };
  }, []);
  return (
    <Dialog open={unavailable}>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Connection lost</DialogTitle>
          <DialogDescription>
            Cannot reach Flowfield. Check that the service is running.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            disabled={checking}
            onClick={async () => {
              setChecking(true);
              try {
                await checkServiceConnection({ immediate: true });
              } finally {
                setChecking(false);
              }
            }}
          >
            {checking ? "Checking connection…" : "Try reconnecting"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
