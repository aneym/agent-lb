#!/usr/bin/env node
// Real-process integration test; no production socket, config, or credentials.
// Usage: node tests/integration/codex_broker_idle_reap.mjs [--standin-only|--delivery-only]
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const source = process.env.CODEX_PLUGIN_CC_DIR ?? path.join(os.homedir(), '.agent-lb/plugins/codex-plugin-cc');
const root = fs.mkdtempSync('/private/tmp/broker-reap-');
const plugin = path.join(root, 'plugin');
const idle = 1500;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
console.log(`Evidence directory: ${root}`);

function command(exe, args, options = {}) {
  const result = spawnSync(exe, args, { encoding: 'utf8', ...options });
  assert.equal(result.status, 0, `${exe} ${args.join(' ')}: ${result.error?.message ?? result.stderr}`);
  return result.stdout;
}
function table() {
  return command('ps', ['-axo', 'pid=,ppid=,pgid=,stat=,comm=']).trim().split('\n').map(line => {
    const [pid, ppid, pgid, stat, ...comm] = line.trim().split(/\s+/);
    return { pid: +pid, ppid: +ppid, pgid: +pgid, stat, comm: comm.join(' ') };
  });
}
let preexisting;
function descendants(root) {
  const rows = table();
  const tree = new Set([root]);
  for (let grew = true; grew;) {
    grew = false;
    for (const p of rows) if (tree.has(p.ppid) && !tree.has(p.pid)) { tree.add(p.pid); grew = true; }
  }
  return [...tree];
}
function snapshot(label, pids, groups = []) {
  const rows = table().filter(p => pids.includes(p.pid) || groups.includes(p.pgid));
  console.log(`${label}: ps -axo pid=,ppid=,pgid=,stat=,comm=\n${JSON.stringify(rows, null, 2)}`);
  return rows;
}
async function until(fn, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await fn()) return;
    await sleep(50);
  }
  throw new Error(`Timed out after ${timeout}ms`);
}
fs.mkdirSync(plugin);
// git archive reads committed sources only and never changes the installed copy.
const archive = path.join(root, 'source.tar');
command('git', ['-C', source, 'archive', '--format=tar', '-o', archive, 'HEAD']);
command('tar', ['-xf', archive, '-C', plugin]);
const apply = () => spawnSync(path.join(repo, 'scripts/apply-codex-plugin-cc-patch.sh'), [], {
  env: { ...process.env, CODEX_PLUGIN_CC_DIR: plugin }, encoding: 'utf8',
});
let applied = apply();
assert.equal(applied.status, 0, applied.stderr);
console.log(applied.stdout.trim());
assert.match(apply().stdout, /Already patched/);
const brokerFile = path.join(plugin, 'plugins/codex/scripts/app-server-broker.mjs');
const clientFile = path.join(plugin, 'plugins/codex/scripts/lib/app-server.mjs');
command('node', ['--check', brokerFile]);
command('node', ['--check', clientFile]);
// Remove patch, break a hunk, and prove rejection changes neither target.
command('git', ['-C', plugin, 'apply', '--reverse', path.join(repo, 'patches/codex-plugin-cc/idle-reap.patch')]);
const unpatched = fs.readFileSync(brokerFile, 'utf8');
fs.writeFileSync(brokerFile, unpatched.replace('  const sockets = new Set();', '  const upstreamChangedSockets = new Set();'));
const before = [brokerFile, clientFile].map(p => fs.readFileSync(p, 'utf8'));
const rejected = apply();
assert.notEqual(rejected.status, 0);
assert.match(rejected.stderr, /app-server-broker\.mjs/);
assert.deepEqual([brokerFile, clientFile].map(p => fs.readFileSync(p, 'utf8')), before);
console.log(`Drift rejection (expected nonzero=${rejected.status}):\n${rejected.stderr.trim()}`);
fs.writeFileSync(brokerFile, unpatched);
applied = apply();
assert.equal(applied.status, 0, applied.stderr);
console.log('PASS checked application, idempotence, atomic drift rejection');
for (const value of ['0', '-1', 'NaN', '1.5', '2147483648', '']) {
  const result = spawnSync(process.execPath, [brokerFile, 'serve', '--endpoint', `unix:${root}/invalid.sock`], {
    env: { PATH: '/usr/bin:/bin', CODEX_COMPANION_BROKER_IDLE_TIMEOUT_MS: value }, encoding: 'utf8', timeout: 5000,
  });
  assert.equal(result.status, 1, result.error?.message ?? result.stderr);
  assert.match(result.stderr, /CODEX_COMPANION_BROKER_IDLE_TIMEOUT_MS must be/);
}
console.log('PASS invalid idle windows rejected before app-server spawn');
if (process.argv.includes('--delivery-only')) process.exit(0);
try {
  preexisting = new Set(table().map(p => p.pid));
} catch (error) {
  console.error(`BLOCKED before spawning brokers: process inventory unavailable: ${error.message}`);
  process.exit(1);
}

// A real, TERM-resistant process tree for escalation coverage. Only the
// protocol responder is a stand-in; all spawning, sockets and signals are real.
const fixture = path.join(root, 'fixture.mjs');
fs.writeFileSync(fixture, `
import fs from 'node:fs';
import readline from 'node:readline';
import {spawn} from 'node:child_process';
const mode = process.argv[2];
if (mode === 'leaf') {
  process.on('SIGTERM', () => fs.appendFileSync(process.env.TERM_LOG, process.pid+'\\n'));
  fs.appendFileSync(process.env.ORPHAN ? process.env.ORPHAN_LOG : process.env.PID_LOG, process.pid+'\\n');
  setInterval(() => {}, 1000);
} else if (mode === 'middle') {
  // Exits at once so its leaf is re-parented to init but stays in this group.
  spawn(process.execPath, [import.meta.filename, 'leaf'], {stdio:'ignore', env:{...process.env, ORPHAN:'1'}});
  process.exit(0);
} else {
  if (mode === 'orphan') spawn(process.execPath, [import.meta.filename, 'middle'], {stdio:'ignore', env:process.env});
  spawn(process.execPath, [import.meta.filename, 'leaf'], {stdio:'ignore', env:process.env});
  fs.appendFileSync(process.env.PID_LOG, process.pid+'\\n');
  if (mode === 'stubborn') process.on('SIGTERM', () => fs.appendFileSync(process.env.TERM_LOG, process.pid+'\\n'));
  const rl = readline.createInterface({input:process.stdin});
  rl.on('line', line => {
    const m = JSON.parse(line);
    if (m.id === undefined) return;
    const result = m.method === 'initialize' && mode === 'mcp'
      ? {protocolVersion:m.params.protocolVersion, capabilities:{tools:{}}, serverInfo:{name:'idle-reap-test',version:'1'}}
      : m.method === 'tools/list' ? {tools:[]} : {};
    process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:m.id,result})+'\\n');
  });
  setInterval(() => {},1000);
}
`);

function safeGroupKill(pgid, signal) {
  assert(!preexisting.has(pgid), `Refusing pre-existing PGID ${pgid}`);
  try { process.kill(-pgid, signal); } catch (e) { if (e.code !== 'ESRCH') throw e; }
}
// A ps stub on the broker's PATH. 'fail' exits nonzero. 'mutate' answers the
// first call truthfully, then reports a new start time for the pid in
// mutate.pid: to the broker that pid now belongs to a different process, as
// after pid reuse. Simulated, because real pid or group reuse is not forceable.
function writePsStub(bin, dir, kind) {
  const stub = kind === 'fail' ? '#!/bin/sh\nexit 1\n' : `#!${process.execPath}
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const count = path => { const n = (fs.existsSync(path) ? +fs.readFileSync(path, 'utf8') : 0) + 1; fs.writeFileSync(path, String(n)); return n; };
const r = spawnSync('/bin/ps', process.argv.slice(2), {encoding:'utf8', env:process.env});
let out = r.stdout;
const target = ${JSON.stringify(path.join(dir, 'mutate.pid'))};
if (fs.existsSync(target) && count(${JSON.stringify(path.join(dir, 'ps.calls'))}) >= 2) {
  // lstart is space padded; match up to end of line.
  const pid = fs.readFileSync(target, 'utf8').trim();
  out = out.split('\\n').map(l => l.trim().split(/\\s+/)[0] === pid ? l.replace(/[A-Z][a-z]{2} [A-Z][a-z]{2} +\\d+ [\\d:]+ \\d{4}\\s*$/, 'Thu Jan  1 00:00:00 1970') : l).join('\\n');
}
process.stdout.write(out); process.stderr.write(r.stderr); process.exit(r.status ?? 1);
`;
  fs.writeFileSync(path.join(bin, 'ps'), stub, { mode: 0o755 });
}
function alive(pid) {
  try { process.kill(pid, 0); return true; } catch (e) { if (e.code === 'ESRCH') return false; throw e; }
}
async function runCase(label, { binary, mode, connected = false, mcp = false, shutdownRpc = false, signal = false, psStub = null }) {
  const dir = path.join(root, label);
  fs.mkdirSync(dir);
  const home = path.join(dir, 'home');
  const bin = path.join(dir, 'bin');
  fs.mkdirSync(home); fs.mkdirSync(bin);
  if (binary) fs.symlinkSync(binary, path.join(bin, 'codex'));
  else {
    const launcher = `#!/bin/sh\nexec '${process.execPath}' '${fixture}' '${mode}'\n`;
    fs.writeFileSync(path.join(bin, 'codex'), launcher, { mode: 0o755 });
  }
  if (psStub) writePsStub(bin, dir, psStub);
  const pidLog = path.join(dir, 'tree.pids');
  const termLog = path.join(dir, 'term.pids');
  const orphanLog = path.join(dir, 'orphan.pids');
  if (mcp) fs.writeFileSync(path.join(home, 'config.toml'), `
[mcp_servers.idle_reap_test]
command = ${JSON.stringify(process.execPath)}
args = [${JSON.stringify(fixture)}, "mcp"]
[mcp_servers.idle_reap_test.env]
PID_LOG = ${JSON.stringify(pidLog)}
TERM_LOG = ${JSON.stringify(termLog)}
`);
  const endpoint = path.join(dir, 'b.sock');
  const pidFile = path.join(dir, 'b.pid');
  const log = fs.openSync(path.join(dir, 'broker.log'), 'w');
  const env = {
    PATH: `${bin}:/opt/homebrew/bin:/usr/bin:/bin`, HOME: home, CODEX_HOME: home,
    TMPDIR: root, CODEX_COMPANION_BROKER_IDLE_TIMEOUT_MS: String(idle),
    PID_LOG: pidLog, TERM_LOG: termLog, ORPHAN_LOG: orphanLog,
  };
  const broker = spawn(process.execPath, [brokerFile, 'serve', '--endpoint', `unix:${endpoint}`, '--cwd', dir, '--pid-file', pidFile], {
    cwd: dir, env, stdio: ['ignore', log, log], detached: true,
  });
  fs.closeSync(log);
  let exited = false;
  const exit = new Promise((resolve, reject) => {
    broker.once('error', error => { exited = true; reject(error); });
    broker.once('exit', (code, sig) => { exited = true; resolve({ code, sig }); });
  });
  // Keep early spawn failures handled until the test awaits the exit result.
  exit.catch(() => {});
  const sockets = [];
  let appPid;
  let observed = [];
  let survivors = [];
  const start = Date.now();
  try {
    await until(() => {
      const child = table().find(p => p.ppid === broker.pid);
      if (child) {
        assert(!preexisting.has(child.pid));
        assert.equal(child.pgid, child.pid, 'Private app-server group');
        appPid = child.pid;
      }
      assert(!exited, `Early exit: ${fs.readFileSync(path.join(dir, 'broker.log'), 'utf8')}`);
      return fs.existsSync(endpoint);
    }, 15000);
    const ready = Date.now();
    appPid = table().find(p => p.ppid === broker.pid)?.pid;
    assert(appPid, 'App-server child must exist');
    assert(!preexisting.has(appPid));
    assert.equal(table().find(p => p.pid === appPid).pgid, appPid, 'Private app-server group');
    async function connect() {
      const socket = net.createConnection(endpoint);
      sockets.push(socket);
      await new Promise((resolve, reject) => { socket.once('connect', resolve); socket.once('error', reject); });
      socket.setEncoding('utf8');
      return socket;
    }
    async function rpc(socket, id, method, params = {}) {
      return new Promise((resolve, reject) => {
        let buffer = '';
        const timer = setTimeout(() => { socket.off('data', read); reject(new Error(`RPC timeout: ${method}`)); }, 10000);
        function read(chunk) {
          buffer += chunk;
          let index;
          while ((index = buffer.indexOf('\n')) >= 0) {
            const response = JSON.parse(buffer.slice(0, index)); buffer = buffer.slice(index + 1);
            if (response.id === id) {
              clearTimeout(timer); socket.off('data', read);
              if (response.error) reject(new Error(JSON.stringify(response.error))); else resolve(response.result);
            }
          }
        }
        socket.on('data', read);
        socket.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
      });
    }
    let client;
    if (connected) {
      client = await connect();
      assert.equal((await rpc(client, 1, 'initialize')).userAgent, 'codex-companion-broker');
      if (mcp) {
        await rpc(client, 2, 'thread/start', { cwd: dir });
        await until(() => fs.existsSync(pidLog) && fs.readFileSync(pidLog, 'utf8').trim().split('\n').length >= 2);
      }
      await sleep(idle * 2 + 200);
      assert(!exited, 'Connected broker exited after idle window');
      snapshot(`${label} still alive after ${Date.now()-ready}ms connected (idle=${idle})`, [broker.pid], [appPid]);
      const second = await connect();
      await rpc(second, 3, 'initialize');
      client.destroy();
      await sleep(idle + 200);
      assert(!exited, 'Closing one of two clients must not arm idle exit');
      second.destroy();
      await sleep(idle / 2);
      client = await connect();
      await rpc(client, 4, 'initialize');
      await sleep(idle + 200);
      assert(!exited, 'Reconnect must cancel old timer');
    } else if (!binary) {
      await until(() => fs.existsSync(pidLog) && fs.readFileSync(pidLog, 'utf8').trim().split('\n').length >= 2);
    }
    const leaf = () => fs.readFileSync(pidLog, 'utf8').trim().split('\n').map(Number).find(pid => pid !== appPid);
    if (mode === 'orphan') {
      await until(() => fs.existsSync(orphanLog) && table().find(p => p.pid === +fs.readFileSync(orphanLog, 'utf8'))?.ppid === 1);
      const orphan = +fs.readFileSync(orphanLog, 'utf8');
      const row = table().find(p => p.pid === orphan);
      assert.equal(row.pgid, appPid, 'Orphan must share the app-server group');
      survivors = [orphan];
      console.log(`${label} unrecorded orphan ${orphan}: ppid=${row.ppid} pgid=${row.pgid}, shares group with recorded app-server`);
    } else if (psStub === 'fail') {
      survivors = [leaf()];
      console.log(`${label} ps stub fails; group member ${survivors[0]} must not get a group signal`);
    } else if (psStub === 'mutate') {
      survivors = [leaf()];
      fs.writeFileSync(path.join(dir, 'mutate.pid'), String(survivors[0]));
      console.log(`${label} ps stub reports a new start time for ${survivors[0]} after the first call (simulated pid reuse)`);
    }
    // Codex puts MCP servers in their own groups, so the broker's tree, not
    // one group, is what shutdown must reap.
    observed = snapshot(`${label} before shutdown`, descendants(broker.pid), [appPid]).map(p => p.pid).filter(pid => !survivors.includes(pid));
    if (fs.existsSync(pidLog)) {
      const fixturePids = fs.readFileSync(pidLog, 'utf8').trim().split('\n').map(Number);
      for (const pid of fixturePids) {
        assert(observed.includes(pid) || survivors.includes(pid), `Fixture PID ${pid} is not a broker descendant; test setup is wrong`);
      }
      if (mcp) {
        const groups = new Set(table().filter(p => fixturePids.includes(p.pid)).map(p => p.pgid));
        assert(![...groups].every(g => g === appPid), 'Expected an MCP server outside the app-server group');
        console.log(`${label} fixture groups outside app-server group: ${[...groups].filter(g => g !== appPid).join(', ')}`);
      }
    }
    const lastDisconnect = Date.now();
    if (shutdownRpc) await rpc(client, 5, 'broker/shutdown');
    else if (signal) process.kill(broker.pid, 'SIGTERM');
    else client?.destroy();
    await until(() => exited, idle + 6000);
    assert.deepEqual(await exit, { code: 0, sig: null });
    if (!connected) assert(Date.now() - ready >= idle - 150, 'No-client timer fired early');
    else if (!shutdownRpc && !signal) assert(Date.now()-lastDisconnect >= idle - 100, 'Disconnect timer fired early');
    await until(() => !table().some(p => !survivors.includes(p.pid) && (observed.includes(p.pid) || p.pgid === appPid)), 5000);
    assert(!fs.existsSync(endpoint), 'Socket must be removed');
    assert(!fs.existsSync(pidFile), 'PID file must be removed');
    snapshot(`${label} after exit`, observed, [appPid]);
    if (!binary) {
      assert(fs.existsSync(termLog), 'TERM must reach resistant descendant');
      console.log(`${label} TERM recipients: ${fs.readFileSync(termLog, 'utf8').trim().replaceAll('\n', ', ')}`);
      assert(Date.now()-lastDisconnect >= 2800, 'KILL escalation must allow TERM grace');
    }
    const termed = fs.existsSync(termLog) ? fs.readFileSync(termLog, 'utf8').trim().split('\n').map(Number) : [];
    for (const pid of survivors) {
      assert(alive(pid), `${pid} is not ours to kill and must survive`);
      assert(!termed.includes(pid), `${pid} is not ours to signal and must not get TERM`);
      console.log(`${label} survivor ${pid} alive, never signalled`);
    }
    console.log(`PASS ${label}: broker=${broker.pid} app-server=${appPid}, total=${Date.now()-start}ms; all observed PIDs absent`);
  } finally {
    for (const s of sockets) s.destroy();
    for (const pid of survivors) {
      assert(pid > 1 && !preexisting.has(pid));
      try { process.kill(pid, 'SIGKILL'); } catch (e) { if (e.code !== 'ESRCH') throw e; }
    }
    // Kill only groups created by this test, never discover-and-kill by name.
    if (appPid) safeGroupKill(appPid, 'SIGKILL');
    if (fs.existsSync(pidLog)) {
      for (const pid of fs.readFileSync(pidLog, 'utf8').trim().split('\n').map(Number)) {
        assert(Number.isInteger(pid) && pid > 1 && !preexisting.has(pid));
        try { process.kill(pid, 'SIGKILL'); } catch (e) { if (e.code !== 'ESRCH') throw e; }
      }
    }
    if (!exited) { safeGroupKill(broker.pid, 'SIGKILL'); await exit; }
  }
}

try {
  if (!process.argv.includes('--standin-only')) {
    const binary = fs.realpathSync(process.env.CODEX_TEST_BINARY ?? path.join(os.homedir(), '.codex/packages/standalone/current/bin/codex'));
    console.log(`Real Codex: ${binary}\n${command(binary, ['--version']).trim()}`);
    await runCase('A-real-zero-clients', { binary });
    await runCase('B-real-connected', { binary, connected: true, mcp: true });
  }
  await runCase('C-resistant-leader', { mode: 'stubborn' });
  await runCase('D-exited-leader', { mode: 'normal' });
  await runCase('E-shutdown-rpc', { mode: 'stubborn', connected: true, shutdownRpc: true });
  await runCase('F-signal-shutdown', { mode: 'normal', connected: true, signal: true });
  await runCase('G-unrecorded-group-member', { mode: 'orphan' });
  await runCase('H-ps-unavailable', { mode: 'stubborn', psStub: 'fail' });
  await runCase('I-simulated-pid-reuse', { mode: 'stubborn', psStub: 'mutate' });
  console.log('PASS all broker idle-reap integration cases');
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
