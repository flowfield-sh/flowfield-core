// One live connection per browser origin, shared by all Flowfield tabs.
// The browser owns this worker's lifetime; each page releases its port on exit.
const ports = new Set<MessagePort>();
let source: EventSource | undefined;
let retry: ReturnType<typeof setTimeout> | undefined;
let delay = 1000;
function broadcast(type: string, data?: string) {
  for (const port of ports) port.postMessage({ type, data });
}
function connect() {
  clearTimeout(retry);
  source?.close();
  source = new EventSource("/api/events");
  source.onopen = () => {
    clearTimeout(retry);
    delay = 1000;
    broadcast("open");
  };
  source.onerror = () => {
    broadcast("error");
    clearTimeout(retry);
    retry = setTimeout(connect, delay);
    delay = Math.min(delay * 2, 15000);
  };
  for (const type of ["change", "activity", "usage", "notification"])
    source.addEventListener(type, (event) =>
      broadcast(type, (event as MessageEvent<string>).data),
    );
}
const worker = self as unknown as {
  onconnect: (event: MessageEvent) => void;
};
worker.onconnect = (event) => {
  const port = event.ports[0];
  ports.add(port);
  port.onmessage = (message) => {
    if (message.data !== "close") return;
    ports.delete(port);
    port.close();
    if (!ports.size) {
      clearTimeout(retry);
      source?.close();
      source = undefined;
    }
  };
  if (!source) connect();
  else if (source.readyState === EventSource.OPEN) {
    port.postMessage({ type: "open" });
    // A joining/recovered tab may have missed writes before its port attached.
    port.postMessage({
      type: "change",
      data: JSON.stringify({ workspaces: null }),
    });
  } else if (source.readyState === EventSource.CLOSED) {
    source.close();
    connect();
  }
};
export {};
