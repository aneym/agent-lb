import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

const installed = path.join(os.homedir(), '.agent-lb/plugins/codex-plugin-cc');
const fixture = fs.mkdtempSync(path.join(process.env.GUARD_CHECK_TMPDIR ?? os.tmpdir(), 'guard-check-'));
const script = 'plugins/codex/scripts/codex-companion.mjs';
try {
  fs.cpSync(path.join(installed, 'plugins/codex'), path.join(fixture, 'plugins/codex'), { recursive: true });
  const upstream = spawnSync('git', ['show', `HEAD:${script}`], { cwd: installed, encoding: 'utf8' });
  assert.equal(upstream.status, 0, upstream.stderr);
  fs.writeFileSync(path.join(fixture, script), upstream.stdout);
  const patch = process.env.GUARD_CHECK_PATCH ?? path.resolve('patches/codex-plugin-cc/repo-write-guard.patch');
  const applied = spawnSync('git', ['apply', patch], { cwd: fixture, encoding: 'utf8' });
  assert.equal(applied.status, 0, applied.stderr);
  const wrapper = path.join(process.env.GUARD_INSTALLED === '1' ? installed : fixture, script);
  const repo = path.join(fixture, 'repo');
  const lane = path.join(fixture, 'lane');
  fs.mkdirSync(repo);
  fs.mkdirSync(lane);
  const initialized = spawnSync('git', ['init', repo], { encoding: 'utf8' });
  assert.equal(initialized.status, 0, initialized.stderr);
  const env = { ...process.env, CODEX_COMPANION_DEPTH: '0', GIT_DIR: undefined, GIT_WORK_TREE: undefined };
  function run(label, cwd, args, refused = false) {
    const result = spawnSync(process.execPath, [wrapper, 'task', '--dry-run', ...args], { cwd, env, encoding: 'utf8' });
    assert.equal(result.status, refused ? 1 : 0, `${label}: ${result.stdout}${result.stderr}`);
    if (refused) {
      assert.match(result.stderr, /repo edits go through seat-run/);
      assert.equal(result.stderr.trim().split('\n').length, 1);
      assert.equal(result.stdout, '');
    } else {
      assert.match(result.stdout, /dry-run passed/);
    }
    console.log(`PASS ${label}`);
  }
  run('read-only repo cwd allowed', repo, ['inspect']);
  run('write repo cwd refused immediately', repo, ['--write', 'inspect'], true);
  run('write lane cwd allowed with repository references', lane, ['--write', 'Inspect /Volumes/StudioExt/repos/agent-lb']);
  run('background repo write refused before queue', repo, ['--write', '--background', 'inspect'], true);
  run('explicit repo write override allowed', repo, ['--write', '--allow-repo-write', 'inspect']);
  console.log('All 5 companion launch checks passed');
} finally {
  fs.rmSync(fixture, { recursive: true, force: true });
}
