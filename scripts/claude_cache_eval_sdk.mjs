// SDK arm of scripts/claude_cache_eval.py. One query() for T1–T4, then a resumed query() for T5.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import os from 'node:os';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function arg(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) return fallback;
  return process.argv[index + 1];
}

function redact(text) {
  return String(text).replace(/sk-[A-Za-z0-9_-]+/g, '<redacted>');
}

const sdkDir = arg('--sdk-dir', process.env.CLAUDE_AGENT_SDK_DIR || path.join(os.homedir(), '.agent-lb', 'evals', 'sdk'));
const model = arg('--model', 'sonnet');
const idleSeconds = Number(arg('--idle-seconds', '360'));
const launcher = arg('--launcher', path.join(os.homedir(), '.local', 'bin', 'claude-lb-launch'));
const workdir = arg('--workdir', path.join(os.homedir(), '.agent-lb', 'evals', 'cache-eval'));

let entry;
try {
  const req = createRequire(path.join(sdkDir, 'package.json'));
  entry = req.resolve('@anthropic-ai/claude-agent-sdk');
} catch (err) {
  console.error(`npm i --prefix ${sdkDir} @anthropic-ai/claude-agent-sdk`);
  console.error(redact(err && err.stack || err));
  process.exit(2);
}

let query;
try {
  ({ query } = await import(pathToFileURL(entry).href));
} catch (err) {
  const message = redact((err && err.message) || err);
  console.error(`SDK failed to load: ${message}`);
  process.exit(2);
}

const env = { ...process.env, CLAUDE_LB_REQUIRE: '1', AGENT_LB_SEAT: 'cache-eval', DISABLE_AUTOUPDATER: '1' };
for (const key of ['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_CUSTOM_HEADERS', 'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN',
  'CLAUDECODE', 'CLAUDE_CODE_CHILD_SESSION', 'HTTPS_PROXY', 'https_proxy', 'NODE_EXTRA_CA_CERTS', 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_LB_SESSION_ID',
  'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_AGENT_SDK_VERSION', 'AGENT_LB_INTENT', 'AGENT_LB_LANE', 'HERDR_TAB_ID']) delete env[key];

const opts = (extra) => ({
  cwd: workdir,
  pathToClaudeCodeExecutable: launcher,
  settingSources: ['user', 'project', 'local'],
  model,
  effort: 'low',
  env,
  canUseTool: async (_tool, input) => ({ behavior: 'allow', updatedInput: input }),
  stderr: (data) => {
    if (/agent-lb|cc:|account|route/i.test(data)) {
      process.stderr.write('[stderr] ' + redact(data).slice(0, 240) + '\n');
    }
  },
  ...extra,
});

const msg = (text) => ({ type: 'user', message: { role: 'user', content: text }, parent_tool_use_id: null, session_id: '' });

let waiters = [];
let session = null;
let resumedSession = null;
const marks = [];
let open = null;

const onResult = () => new Promise((resolve) => waiters.push(resolve));

function startTurn(turn) {
  open = { turn, startedAt: Date.now(), endedAt: null };
  marks.push(open);
}

async function* turns() {
  startTurn('T1');
  yield msg('Reply with exactly: ONE');
  await onResult();
  await sleep(5000);
  startTurn('T2');
  yield msg('Reply with exactly: TWO');
  await onResult();
  await sleep(5000);
  startTurn('T3');
  yield msg('Reply with exactly: THREE');
  await onResult();
  await sleep(idleSeconds * 1000);
  startTurn('T4');
  yield msg('Reply with exactly: FOUR');
  await onResult();
}

async function* resumeTurn() {
  startTurn('T5');
  yield msg('Reply with exactly: FIVE');
  await onResult();
}

async function drain(stream, resumed) {
  for await (const message of stream) {
    if (message.type === 'system' && message.subtype === 'init') {
      if (resumed) resumedSession = message.session_id;
      else session = message.session_id;
    }
    if (message.type === 'result') {
      if (open) open.endedAt = Date.now();
      const waiter = waiters.shift();
      waiter?.();
    }
  }
}

try {
  await drain(query({ prompt: turns(), options: opts({}) }), false);
  if (!session) throw new Error('sdk query returned no session');
  await sleep(3000);
  await drain(query({ prompt: resumeTurn(), options: opts({ resume: session }) }), true);
  console.log(JSON.stringify({ session, resumedSession, marks }));
} catch (err) {
  console.error(redact(err && err.stack || err));
  process.exit(1);
}
