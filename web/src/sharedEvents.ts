/** Keep the normal EventSource interface and recovery owner in App. */
export function liveEvents(): Pick<
  EventSource,
  "onopen" | "onerror" | "addEventListener" | "close"
> {
  if (typeof SharedWorker === "undefined")
    return new EventSource("/api/events");
  const worker = new SharedWorker(
    new URL("./sharedEvents.worker.ts", import.meta.url),
    { type: "module" },
  );
  const events = new EventTarget();
  const client = {
    onopen: null as EventSource["onopen"],
    onerror: null as EventSource["onerror"],
    addEventListener: events.addEventListener.bind(
      events,
    ) as EventSource["addEventListener"],
    close() {
      worker.port.postMessage("close");
      worker.port.close();
    },
  };
  worker.port.onmessage = ({
    data,
  }: MessageEvent<{ type: string; data?: string }>) => {
    const event = new MessageEvent(data.type, { data: data.data });
    if (data.type === "open") client.onopen?.call(client as EventSource, event);
    else if (data.type === "error")
      client.onerror?.call(client as EventSource, event);
    else events.dispatchEvent(event);
  };
  worker.onerror = (event) =>
    client.onerror?.call(client as EventSource, event);
  return client;
}
