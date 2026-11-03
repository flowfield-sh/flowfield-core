import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { state } from "./support";

// Seed pre-retirement records directly: these are no longer public operations.
// Browser journeys still verify that saved history, answers and drafts survive.
function historicalQuestionWrite(
  operation: string,
  args: Record<string, unknown>,
) {
  return JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [
        "-c",
        `
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent / 'tests'))
from historical_questions import seed_question, seed_follow_up
from flowfield.application import Workspace
from flowfield.questions import Questions, QuestionCreate
args=json.load(sys.stdin)
questions=Questions(Workspace(Path(sys.argv[1])))
if sys.argv[2]=='ask':
    value=seed_question(questions, args['workspace_id'], QuestionCreate.model_validate(args['question']))
else:
    value=seed_follow_up(questions, args['workspace_id'], args['question_id'], args['follow_up'])
print(value.model_dump_json())
`,
        state,
        operation,
      ],
      { input: JSON.stringify(args), encoding: "utf8" },
    ),
  );
}
export const historicalQuestion = (args: Record<string, unknown>) =>
  historicalQuestionWrite("ask", args);
export const historicalQuestionFollowUp = (args: Record<string, unknown>) =>
  historicalQuestionWrite("follow-up", args);

export async function historicalQuestionResponse(
  path: string,
  options: { data: Record<string, unknown> },
) {
  const match = path.match(/\/workspaces\/([^/]+)\/questions$/);
  if (!match)
    throw new Error("Expected an exact historical question collection path");
  const value = historicalQuestion({
    workspace_id: decodeURIComponent(match[1]),
    question: options.data,
  });
  return {
    ok: () => true,
    status: () => 201,
    json: async () => value,
    text: async () => JSON.stringify(value),
  };
}
