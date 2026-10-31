// Bounded public arguments for Flowfield operations, never arbitrary native payloads.
const fields = new Set((
  'workspace_id project_id task_id subtask_id document_id result_id review_id run_id ' +
  'source source_id section mode offset end limit revision expected_revision after before ' +
  'query title description acceptance_criteria body summary evidence request action note ' +
  'completion status dependencies documents input_files labels id purpose required ' +
  'enabled max_parallel auto_start_queue target_branch create_from runtime checks setup_commands ' +
  'setup_timeout_seconds check_timeout_seconds harness model effort fast ' +
  'candidate_commit author before_id reason path name file_id files ' +
  'task subtask changes settings change priority review selection reconciliation ' +
  'projectless projects content outcome limitations remaining_work'
).split(' '));

function publicValue(value, depth = 0) {
  if (depth > 5) return '[nested arguments omitted]';
  if (typeof value === 'string') return value.length > 12000 ? value.slice(0, 12000) + '… [truncated]' : value;
  if (value === null || typeof value === 'boolean' || typeof value === 'number') return value;
  if (Array.isArray(value)) return [...value.slice(0, 20).map(item => publicValue(item, depth + 1)), ...(value.length > 20 ? ['[more items omitted]'] : [])];
  if (!value || typeof value !== 'object') return '[unsupported value]';
  const entries = Object.entries(value);
  const result = Object.fromEntries(entries.filter(([key]) => fields.has(key)).slice(0, 40).map(([key, item]) => [key, publicValue(item, depth + 1)]));
  if (Object.keys(result).length < entries.length) result._omitted = 'Additional arguments not displayed';
  return result;
}

export function toolDetails(name, args) {
  const value = args && typeof args === 'object' ? args : {};
  if (/^mcp__flowfield(?:_[a-f0-9]{16}|_[a-f0-9]{32})?__\w+$/.test(name)) {
    const text = JSON.stringify(publicValue(value), null, 2);
    return { Arguments: text.length > 14000 ? text.slice(0, 13950) + '\n… [arguments truncated]' : text };
  }
  return Object.fromEntries(['command', 'cwd', 'file_path', 'path', 'url'].filter(key => typeof value[key] === 'string').map(key => [key, value[key].length > 4000 ? value[key].slice(0, 3980) + '… [truncated]' : value[key]]));
}
