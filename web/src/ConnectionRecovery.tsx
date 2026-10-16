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
            Cannot reach Flowfield. Check that the service is running. We’ll
            reconnect automatically.
          </DialogDescription>
        </DialogHeader>
        <p>
          Your open views and drafts stay here. Interrupted actions won’t be
          sent again automatically.
        </p>
        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            disabled={checking}
            onClick={async () => {
              setChecking(true);
              try {
                await checkServiceConnection();
              } finally {
                setChecking(false);
              }
            }}
          >
            {checking ? "Checking connection…" : "Reconnect now"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
