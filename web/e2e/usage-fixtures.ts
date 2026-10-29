/** Synthetic accounting contract examples, not evidence of live-provider completeness. */
import type { UsageView, UsageExecution, Tokens } from "../src/usage";

export const unknownTokens: Tokens = {
  input: null,
  output: null,
  cache_read: null,
  cache_write: null,
  reasoning: null,
  total: null,
};
export const tokens = (total: number | null): Tokens =>
  total == null
    ? { ...unknownTokens }
    : { ...unknownTokens, input: total * 0.9, output: total * 0.1, total };
const start = "2026-10-27T10:00:00+00:00";
export function usageFixture(workspace: string, task: string): UsageView {
  const amounts = [2000, 450, 300, 800, 150, 100, null];
  const items: UsageExecution[] = amounts.map((total, index) => ({
    number: index + 1,
    workspace_id: workspace,
    source: [2, 4].includes(index) ? "review_attempt" : "run",
    source_id: `execution-${index + 1}`,
    task_id: task,
    related_task_id: null,
    project_id: null,
    parent_id: [2, 4].includes(index) ? "review-1" : null,
    subtask_id: index === 3 ? "subtask-1" : null,
    role: index === 2 ? "reviewer" : "worker",
    purpose: [
      "implementation",
      "answer-continuation",
      "assessment",
      "correction",
      "implementer-clarification",
      "retry",
      "retry",
    ][index],
    outcome: index === 5 ? "failed" : index === 6 ? "stopped" : "completed",
    harness: [
      "claude-code",
      "codex",
      "claude-code",
      "pi",
      "claude-code",
      "codex",
      "pi",
    ][index],
    requested_model: "requested-model",
    session_id: `session-${index}`,
    created_at: start,
    started_at: start,
    ended_at:
      index === 6
        ? null
        : `2026-10-27T10:${String([20, 5, 8, 10, 3, 2][index]).padStart(2, "0")}:00+00:00`,
    timing: index === 6 ? "unknown-end" : "recorded",
    legacy: false,
    coverage:
      index === 6
        ? "unavailable"
        : [3, 5].includes(index)
          ? "partial"
          : "complete",
    reasons:
      index === 6
        ? ["missing-counters", "cleanup-unconfirmed"]
        : index === 3
          ? ["native-provider-coverage-unknown"]
          : index === 5
            ? ["native-error"]
            : [],
    tokens: tokens(total),
    observed_models: [1, 3, 5, 6].includes(index) ? [null] : ["reported-model"],
    observation_count: index === 6 ? 0 : 1,
    dispatch_count: 1,
  }));
  return {
    contracts: [
      {
        harness: "claude-code",
        contract: "claude-model-usage/0.3.293",
        native_version: "2.1.293",
        supported: true,
        scope: "Query pipeline including native children",
        limitation:
          "Helpers outside the pipeline are excluded; deterministic fixture only.",
      },
      {
        harness: "codex",
        contract: "codex-thread-total/0.164.0",
        native_version: "0.164.0",
        supported: true,
        scope: "Native thread",
        limitation:
          "Defaulted optional counters remain unknown; context remains separate.",
      },
      {
        harness: "pi",
        contract: "pi-session-stats/1",
        native_version: "1.4.0",
        supported: true,
        scope: "Native session including compacted entries",
        limitation:
          "Provider completeness and reported-model attribution remain unknown.",
      },
    ],
    measured_at: start,
    collection_started_at: start,
    totals: {
      reasons: [],
      tokens: tokens(3800),
      attempts: 7,
      started_attempts: 7,
      unknown_starts: 0,
      complete: 4,
      partial: 2,
      unavailable: 1,
      legacy: 0,
      recorded_seconds: 2880,
      elapsed_seconds: 1200,
      recorded_intervals: 6,
      unknown_intervals: 1,
      live_intervals: 0,
      coverage: "partial",
    },
    related_coordinator_attempts: 1,
    items,
    next_after: null,
    buckets: [],
    series: [],
    breakdowns: [
      {
        dimension: "role",
        key: "worker",
        tokens: tokens(3500),
        attempts: 6,
        elapsed_seconds: 1200,
        live: false,
      },
      {
        dimension: "role",
        key: "reviewer",
        tokens: tokens(300),
        attempts: 1,
        elapsed_seconds: 480,
        live: false,
      },
      {
        dimension: "source",
        key: "run",
        tokens: tokens(3350),
        attempts: 5,
        elapsed_seconds: null,
        live: false,
      },
      {
        dimension: "source",
        key: "review_attempt",
        tokens: tokens(450),
        attempts: 2,
        elapsed_seconds: null,
        live: false,
      },
    ],
    filters: {
      start: null,
      end: null,
      timezone: "UTC",
      group: "day",
      dimension: "role",
      task_id: task,
      project_id: null,
      role: null,
      source: null,
      purpose: null,
      outcome: null,
      harness: null,
      model: null,
      coverage: null,
      related_task_id: null,
      unassigned: false,
      unknown_model: false,
      after: 0,
      limit: 50,
    },
    timing_label:
      "Usage reported during the period; consumption may span earlier periods.",
    limit_reached: false,
  };
}
