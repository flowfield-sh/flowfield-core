import test from 'node:test';
import assert from 'node:assert/strict';
import { toolDetails } from './tool-details.mjs';

test('Flowfield mutations expose intent and destinations without unknown native fields', () => {
  const args = { task: { title: 'Search books', description: 'Match titles', project_id: 'books', documents: [{ id: 'guide', purpose: 'required' }], private: 'SECRET' }, private: 'SECRET' };
  const details = toolDetails('mcp__flowfield_0123456789abcdef__create_task', args).Arguments;
  assert.match(details, /Search books/);
  assert.match(details, /project_id/);
  assert.match(details, /required/);
  assert.match(details, /Additional arguments not displayed/);
  assert.doesNotMatch(details, /SECRET|"private"/);
  assert.equal(args.task.private, 'SECRET'); // display filtering does not modify native input
  const integration = toolDetails('mcp__flowfield__configure_integration', { settings: { target_branch: 'review', create_from: 'main', runtime: 'local' } }).Arguments;
  assert.match(integration, /"create_from": "main"/);
  assert.match(integration, /"runtime": "local"/);
});

test('source navigation exposes ranges and long payloads disclose bounds', () => {
  const details = toolDetails('mcp__flowfield__get_document_text', { document_id: 'guide', revision: 2, offset: 4000, end: 5000, mode: 'read' }).Arguments;
  assert.match(details, /4000/);
  assert.match(details, /5000/);
  const large = toolDetails('mcp__flowfield__edit_task', { changes: { description: 'x'.repeat(50000), acceptance_criteria: 'y'.repeat(50000) } }).Arguments;
  assert.ok(large.length <= 14000);
  assert.match(large, /truncated/);
});

test('unrelated native tools retain the existing narrow public fields', () => {
  for (const name of ['Bash', 'mcp__foreign__create_task', 'mcp__flowfield_imposter__create_task']) {
    assert.deepEqual(toolDetails(name, { command: 'echo public', private: 'SECRET', task: { title: 'PRIVATE' } }), { command: 'echo public' });
  }
  const long = toolDetails('Bash', { command: 'x'.repeat(5000) }).command;
  assert.ok(long.length <= 4000);
  assert.match(long, /truncated/);
});
