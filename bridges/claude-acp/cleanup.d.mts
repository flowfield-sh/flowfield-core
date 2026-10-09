import type { Options, Query } from "@anthropic-ai/claude-agent-sdk";
export const capability: Readonly<{
  version: 1; method: "_flowfield/quiesce"; scope: "native-turns-and-tasks";
}>;
export function parseQuiesce(value: unknown): { sessionId: string };
export type Receipt = typeof capability & {
  sessionId: string; status: "confirmed" | "uncertain"; reason: string | null;
  checkedNativeOwners: number; stoppedTasks: number; quietObservations: number;
  nativeOwnerExited: boolean;
};
type Spawn = NonNullable<Options["spawnClaudeCodeProcess"]>;
export class Cleanup {
  readonly enabled: boolean;
  constructor(state: (sessionId: string) => {
    query: Query; startConsumer(): void; cancel(): Promise<void>; close(): Promise<void>;
  } | undefined, options?: { enabled?: boolean; timeoutMs?: number; maxTasks?: number });
  run<T>(operation: () => T | Promise<T>): T | Promise<T>;
  attach<T>(operation: () => T | Promise<T>, existingId?: string | null): T | Promise<T>;
  spawn(sessionId: string, ...options: Parameters<Spawn>): ReturnType<Spawn>;
  observe(sessionId: string, query: Query, message: unknown): void;
  stop(sessionId: string): Promise<Receipt>;
}
