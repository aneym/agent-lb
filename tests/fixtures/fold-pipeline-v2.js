export const meta = {
  name: 'fold-pipeline-v2',
  description: 'Implement pieces in parallel; each is proven by its check, a cross-vendor verify, a release-facts gate, a user scenario run on base and head (scenario-run), and (with acceptance tests) a fails-on-revert check, re-verified after every fix; after one fix round the final diff gets a decision (pass | held | override | split | founder_call | infra_blocked), never a bare FAIL',
  phases: [
    { title: 'Implement', detail: 'one implementer per piece, in its own worktree' },
    { title: 'Verify', detail: 'test run (for the Codex verifier), cross-vendor refute, release facts' },
    { title: 'Fix', detail: 'one fix round by default; every fix is re-tested, re-verified and re-checked' },
    { title: 'Decide', detail: 'after the last fix round, opus-seat rules on the final diff: pass, override, split or ask a founder; tooling blocks remain infra_blocked' },
  ],
}

// Reject malformed invocation data before any seat or derived argument reads it.
if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch { return { status: 'bad_args', next: 'args must be a JSON object', pieces: [] } }
}
if (args === null || typeof args !== 'object' || Array.isArray(args) ||
    (Object.getPrototypeOf(args) !== Object.prototype && Object.getPrototypeOf(args) !== null))
  return { status: 'bad_args', next: 'args must be a JSON object', pieces: [] }

const LAUNCH = `AUTHORIZED TASK: this computed task is the user's request for you; the user started this workflow to have it done. The relayed user request above it is the launching session's latest chat message, or that session's instruction to run a workflow. It was not written to you and is usually about other work (a question, a link, a tweet, a status note). Do not answer it or act on it, and do not let it narrow, replace or refuse this task. The one exception: if it explicitly tells this workflow's agents to stop or change their work, follow it and say so in your result.`

// orch-lab E4 (2026-09-26). Differences from fold-pipeline.js:
// - Codex-verified pieces get a codex-test-runner run first (codex-verifier is read-only and cannot run tests).
// - A release-facts gate runs after every verify PASS: code_floor (version drift/reuse, compile, boot imports),
//   migrations, deploy config and packaging. The #1360 (no Eve VERSION bump) and #1440 (pyproject test packages broke
//   the Railway image) class of miss is invisible in a diff review.
// - Optional acceptance_tests (paths): the implementer may not edit them, and the gate runs
//   ~/.agent-rails/orch-lab/bin/revert-check.sh, which must show the test FAILS on the base tree.
// - Every piece returns latest_verdict and queue_allowed. queue_allowed is true only when the LATEST verify and the
//   LATEST release-facts both passed. Lanes never queue a piece whose queue_allowed is false (models.md).
// 2026-09-27 (models.md): when fix_rounds >= 2, a Sol piece's round-2 fix goes to opus-seat. The factory scoreboard counted 18 of 32 pieces
// ending needs_orchestrator in the prior 24 hours (34% accept rate). Disable with args.escalate: false.
// 2026-09-27: round 2 of an escalated piece is verified by codex-verifier only: the Opus verifier refuses a brief
// with an Anthropic author (5 of 5 escalations on 2026-09-27 failed on that refusal).
// args: { lane, run_id?: string, hosts?: 'spill'|'auto'|'studio' (agent-rails pieces default to 'spill': Studio while it has room, a job box when Studio is loaded; 'auto' is box-first; 'studio' never uses a box), checks?: 'spill'|'box'|'studio' (governs both the check and scenario runs), money_path_floor?: 'block'|'warn'|'off' (default 'block'), repo?: string, factory_bin?: string, fix_rounds?: nonnegative integer (default 1), escalate?: boolean (default true), panel?: 'all' (correct + risk lenses on every piece, not only the money path), python?: abs path of the repo's Python (default ~/rails-ci/venv/bin/python; another host sets its own), scenario_run?: abs path of scenario-run,
//   pieces: [ { title, worktree, base?: 'origin/main', files: [...], spec, check, checks?: 'spill'|'box'|'studio', scenario?: scenario-run spec object (type pytest | command | mcp | page),
//   implementer?: 'gpt-implementer'|'sonnet-implementer'|'opus-seat' (default: the routed implement head, else gpt-implementer; Sol by policy), host?: 'spill'|'auto'|string, repo?: string, with?: [absolute directory paths], money_path?: bool,
//   acceptance_tests?: [repo-relative paths], proof?: { product, commands: ['<verify args>', ...] }, commit_message? } ] }
// proof (pstack, docs/prd/pstack-2026-09/PLAN.md): for a product-visible change, the verify-CLI commands that show the
// behavior (`python -m scripts.verify <product> <args>`); the implementer runs them and returns the proof.json paths, and
// the verifier reruns them. A missing or failing proof is a must-fix.
// Acceptance tests are proven with scripts/ci/fails_on_base.py when the worktree has it (in-repo, any host), else
// orch-lab's revert-check.sh.
// Each piece returns reviewer and author_vendor (the vendor of the code the last review judged: after an escalation, the
// fixing seat's; original_author_vendor keeps the first implementer's) so the lane can record the verdict on the PR:
//   python3 scripts/queue_pr.py verdict <pr> --pass|--fail --reviewer <reviewer> --author-vendor <author_vendor>
// When same_vendor_review is true, include "same-vendor (Codex empty)" in verdict notes and the PR comment.
// and queue with `python3 scripts/queue_pr.py queue <pr>`, which refuses a FAIL or a verdict on an older diff.
// Never pushes, merges or writes to GitHub. The lane pushes and queues.

// After one fix round by default, opus-seat decides on the final diff against the review bar:
// pass | held | override | split | founder_call | infra_blocked. A no-gate agreed defect splits the piece;
// money-path, one-way and unsettled product calls go to a founder. fix_rounds is opt-in (default 1);
// the decider rules once on the final diff. Tooling failures are retried and never treated as verdicts.
// Every review is cross-vendor to the last code author except a fresh Opus review after both Codex reviewer seats fail as infra; unknown implementers fail preflight.
// Terminal statuses: pass | held | override | split | founder_call | infra_blocked (pipeline_error if the template itself throws).
const SEAT_VENDOR = {
  'gpt-implementer': 'openai', 'codex-verifier': 'openai', 'sol-consult': 'openai',
  'codex-test-runner': 'openai', 'sonnet-implementer': 'anthropic', 'opus-seat': 'anthropic', 'effort-xhigh': 'anthropic',
  verifier: 'anthropic',
}
const IMPLEMENTERS = ['gpt-implementer', 'sonnet-implementer', 'opus-seat', 'effort-xhigh']
// The stand-in after a seat returns nothing or an infra_error twice. Reviewers and gates stay on their vendor, so the
// vendor guard holds until both Codex reviewer seats fail; an implementer goes to its stand-in, and later reviews follow its vendor.
// When Codex is empty (a Sol implementer fails twice on 429s or usage limits), Sonnet 5.5 high stands in first and
// opus-seat after it (E12, 2026-09-28: Sonnet high matches Opus medium on speed and accepts at 27% less cost); Sol stays
// the default. IMPL_FALLBACK carries that two-step order; ALT is the one-step stand-in everywhere else.
const IMPL_FALLBACK = {
  'gpt-implementer': ['sonnet-implementer', 'opus-seat'],
  'sonnet-implementer': ['opus-seat'],
}
const ALT = {
  'gpt-implementer': 'sonnet-implementer', 'sonnet-implementer': 'opus-seat',
  'opus-seat': 'effort-xhigh', 'effort-xhigh': 'opus-seat', verifier: 'opus-seat',
  'codex-verifier': 'sol-consult', 'sol-consult': 'codex-verifier',
  // The test runner only runs the check, so its stand-in is a Sol seat that runs it on the host, outside Codex's sandbox.
  'codex-test-runner': 'gpt-implementer',
}
// args.home (2026-09-28): the home directory of the host running this fold; a Linux lane host (pc-fold) passes /home/jobs.
const HOME_DIR = (args && typeof args.home === 'string' && /^\/[^\s'"`$]+$/.test(args.home) && args.home.replace(/\/$/, '')) || '/Users/aneyman'
const LOCAL_HOST = typeof args.local_host === 'string' && args.local_host ? args.local_host : 'studio'
const PY = (args && args.python) || `${HOME_DIR}/rails-ci/venv/bin/python`
const SCENARIO_RUN = args.scenario_run || `${HOME_DIR}/factory/bin/scenario-run`
const SNAPSHOT = `${HOME_DIR}/.agent-rails/orch-lab/bin/snapshot.sh`
const proofLines = p => p.proof && p.proof.commands && p.proof.commands.length
  ? p.proof.commands.map(c => `${PY} -m scripts.verify ${p.proof.product} ${c}`) : []
// A missing implementer defaults to the head of the routed implement chain (args.routing, which run-workflow.sh merges
// from `route seats --json`), or Sol without routed rules or with a seat this template does not know (2026-09-28).
// Retired implementers are remapped to Sol in preflight; other empty or unknown explicit implementers fail.
const ROUTED_IMPL = args && args.routing && args.routing.classes && args.routing.classes.implement
  && Array.isArray(args.routing.classes.implement.chain) ? (args.routing.classes.implement.chain[0] || {}).seat : null
const DEFAULT_IMPL = IMPLEMENTERS.includes(ROUTED_IMPL) ? ROUTED_IMPL : 'gpt-implementer'
const implOf = p => p.implementer === undefined || p.implementer === null ? DEFAULT_IMPL : p.implementer
const vendorOf = seat => {
  if (!SEAT_VENDOR[seat]) throw new Error(`unknown seat "${seat}": add it to SEAT_VENDOR in fold-pipeline-v2.js before using it`)
  return SEAT_VENDOR[seat]
}
// Escalation follows the seat that actually wrote the code: a Sol stand-in for a Sonnet piece escalates too.
const escalates = seat => args.escalate !== false && vendorOf(seat) === 'openai'
const FIX_ROUNDS = Number.isInteger(args && args.fix_rounds) && args.fix_rounds >= 0 ? args.fix_rounds : 1
const MONEY_FLOOR_MODE = args.money_path_floor === undefined ? 'block' : args.money_path_floor
// args.panel 'all' (2026-09-26): every piece gets the correct + risk panel, any-fail (E7). Both lenses run on the
// reviewer seat, cross-vendor to the code's last author.
const lensesOf = p => p.money_path || args.panel === 'all' ? ['correct', 'risk'] : ['correct']
const reviewSeatFor = author => vendorOf(author) === 'anthropic' ? 'codex-verifier' : 'verifier'
function assertCross(seat, author, sameVendorFallback = false) {
  if (vendorOf(seat) === vendorOf(author) && !(sameVendorFallback && seat === 'verifier' && vendorOf(author) === 'anthropic'))
    throw new Error(`vendor guard: ${seat} (${vendorOf(seat)}) would review code last written by ${author} (${vendorOf(author)})`)
}
// authors[i] is the seat whose code round i reviewed. Consecutive rounds by one seat are grouped.
function authorsLine(authors) {
  if (authors.every(s => s === authors[0])) return `author vendor: ${vendorOf(authors[0])}, seat ${authors[0]}`
  const groups = []
  authors.forEach((seat, i) => {
    const g = groups[groups.length - 1]
    if (g && g.seat === seat) g.to = i
    else groups.push({ seat, from: i, to: i })
  })
  const span = g => g.from === 0 ? (g.to === 0 ? 'round 0' : `rounds 0-${g.to}`) : g.from === g.to ? `fix round ${g.from}` : `fix rounds ${g.from}-${g.to}`
  return `author vendors: ${groups.map(g => `${vendorOf(g.seat)}, seat ${g.seat} (${span(g)})`).join('; ')}`
}
const slug = p => p.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40)
const pieceId = p => ('fold-' + slug(p)).replace(/-+$/, '')
const shellQuote = v => `'${String(v).replace(/'/g, `'\\''`)}'`
const relayPrompt = body => `[class:relay]
${LAUNCH}
Your task: use the Bash tool as directed below. Do not edit, fix or judge the piece's code yourself.
${body}`
const SCENARIO_RELAY = { type: 'object', properties: { line: { type: 'string' }, runner_exit: { type: 'integer' }, infra_error: { type: 'string' } }, required: ['line', 'runner_exit'] }
const PATCH_RELAY = { type: 'object', properties: { apply_exit: { type: 'integer' }, printed: { type: 'string' }, infra_error: { type: 'string' } }, required: ['apply_exit', 'printed'] }
const CARRY_RELAY = { type: 'object', properties: { line: { type: 'string' }, runner_exit: { type: 'integer' }, infra_error: { type: 'string' } }, required: ['line', 'runner_exit'] }
function parseCarry(out) {
  try {
    const r = JSON.parse(out.line)
    return out.runner_exit === 0 && r && typeof r === 'object' && !Array.isArray(r) &&
      ['ok', 'conflict', 'needs_migration', 'infra', 'skipped'].includes(r.status) && Number.isInteger(r.behind) &&
      typeof r.rebased === 'boolean' && typeof r.head === 'string' && Array.isArray(r.conflict_files) &&
      Array.isArray(r.generators) && Array.isArray(r.generated_files) && r.bump && r.floor &&
      typeof r.reason === 'string' ? r : null
  } catch { return null }
}
const baseMechanics = item => /\b(?:behind main|stale base(?: branch| commit)?|rebase onto main|needs? rebas(?:e|ing))\b/i.test(item) && !/\bstale base (?!branch\b|commit\b|is\b|behind\b|needs\b)/i.test(item)
const bumpMechanics = item => /\b(?:version (?:not bumped|bump|reuse)|bump_release|missing (?:a )?version bump)\b/i.test(item)
const trainBumpItem = item => bumpMechanics(item) || /\bversion drift\b/i.test(item)
const generatedMechanics = item => /\b(?:tools\.json|catalog[ -]digests?|plugin zip|generated files? (?:stale|not regenerated))\b/i.test(item)
const releaseMechanics = item => baseMechanics(item) || bumpMechanics(item) || generatedMechanics(item)
function parseScenario(out) {
  try {
    const result = JSON.parse(out.line)
    const exits = { fails_on_base: 0, head_only: 0, passes_on_base: 1, still_fails: 1, infra: 3 }
    return result && typeof result === 'object' && !Array.isArray(result) &&
      Object.hasOwn(exits, result.verdict) && out.runner_exit === exits[result.verdict] &&
      result.base && result.head ? result : null
  } catch { return null }
}
const patchLines = patch => patch.split('\n').filter(line => (line.startsWith('+') && !line.startsWith('+++')) || (line.startsWith('-') && !line.startsWith('---'))).length
const unitOf = p => {
  let hash = 0x811c9dc5
  // FNV-1a over UTF-16 code units (the workflow sandbox has no TextEncoder or crypto).
  const text = `${p.title}\n${p.worktree}`
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return `${slug(p).replace(/^-+|-+$/g, '') || 'piece'}-${(hash >>> 0).toString(16).padStart(8, '0')}`
}

const RESULT = {
  type: 'object',
  properties: {
    done: { type: 'boolean' }, diffstat: { type: 'string' }, check_output_tail: { type: 'string' },
    commit: { type: 'string' }, deviations: { type: 'string' },
    proofs: { type: 'array', items: { type: 'string' } }, infra_error: { type: 'string' },
  },
  required: ['done', 'diffstat', 'check_output_tail', 'commit', 'deviations'],
}
const RUN = {
  type: 'object',
  properties: { out_path: { type: 'string' }, exit_code: { type: 'integer' }, tail: { type: 'string' }, snapshot: { type: 'string' }, infra_error: { type: 'string' } },
  required: ['out_path', 'exit_code', 'tail', 'snapshot'],
}
const VERDICT = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' }, must_fix: { type: 'array', items: { type: 'string' } },
    advisory: { type: 'array', items: { type: 'string' } }, patch: { type: 'string' },
    notes: { type: 'string' }, check_reran: { type: 'string' }, check_exit: { type: 'integer' }, infra_error: { type: 'string' },
  },
  required: ['pass', 'must_fix', 'advisory', 'notes', 'check_reran', 'check_exit'],
}
const FACTS = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' }, must_fix: { type: 'array', items: { type: 'string' } },
    code_floor: { type: 'string' }, versions: { type: 'string' }, migrations: { type: 'string' },
    deploy_config: { type: 'string' }, revert_check: { type: 'string' }, money_path_files: { type: 'string' },
    infra_error: { type: 'string' }, bump_needed: { type: 'boolean' },
  },
  required: ['pass', 'must_fix', 'code_floor', 'versions', 'migrations', 'deploy_config', 'revert_check', 'money_path_files'],
}
const DECISION = {
  type: 'object',
  properties: {
    items: { type: 'array', items: { type: 'object', properties: {
      index: { type: 'integer' }, ruling: { type: 'string', enum: ['agree', 'disagree'] }, reason: { type: 'string' },
    }, required: ['index', 'ruling', 'reason'] } },
    guidance: { type: 'string' },
    one_way: { type: 'boolean' }, one_way_reason: { type: 'string' },
    needs_founder: { type: 'boolean' },
    founder: { type: 'object', properties: {
      title: { type: 'string' }, why: { type: 'string' }, question: { type: 'string' },
      choices: { type: 'array', items: { type: 'string' } }, recommend: { type: 'string' }, recommend_why: { type: 'string' },
    }, required: ['title', 'why', 'question', 'choices', 'recommend', 'recommend_why'] },
    head: { type: 'string' }, tree: { type: 'string' }, infra_error: { type: 'string' },
  },
  required: ['items', 'guidance', 'one_way', 'needs_founder', 'founder', 'head', 'tree'],
}
// Every prompt carries this. A tooling failure is not a verdict: the fold reruns the seat or a stand-in, and never
// spends a fix round on it.
const CANT_RUN = 'check could not run'
const cantRun = out => !!out && (String(out.infra_error || '').trim().toLowerCase().startsWith(CANT_RUN) || [126, 127].includes(out.exit_code) || [126, 127].includes(out.check_exit))
const INFRA = `infra_error: leave it out unless a tooling failure kept you from doing this job (the sandbox refused a command, a worktree or temp dir could not be created, a tool or the model bridge crashed). Then set it to one line saying what failed; the fold reruns you or another seat, and it never counts as a verdict. Never use it for a defect in the code, a failing check, or a file the piece should have made: those are must_fix. The check command itself could not run is also infra: it never started or selected nothing to test (command not found, exit 126 or 127; a file or path it names that the spec does not ask the piece to create; a stray argument the tool rejects before running anything, e.g. pytest exit 4 "file or directory not found" or exit 5 "no tests collected"). Then set infra_error to a line starting "check could not run:" and quote the error. A check that ran and failed, including an import or compile error in the code under test, is a result, not infra.`

const rules = p => `Worktree: ${p.worktree}. Work only there. Never git stash, never touch another worktree, never push, never write to GitHub.
Files you may touch: ${p.files.join(', ')}. Nothing else.${p.acceptance_tests && p.acceptance_tests.length ? `
Acceptance tests (already written, currently failing; NEVER edit, rename, skip or weaken them): ${p.acceptance_tests.join(', ')}. You are done only when they pass.` : ''}
Tests: load ~/.agents/skills/test-audit/SKILL.md before writing or changing any test.
The lane already holds the workboard claim for these files; do not run scripts/workboard.py.
Secrets never appear in anything you write.`

// Checked for every piece before the first agent() call, so a bad piece never starts a seat.
function showEntry(value) {
  try {
    return typeof value === 'string' || (typeof value === 'object' && value !== null) ? JSON.stringify(value) : String(value)
  } catch {
    return Object.prototype.toString.call(value)
  }
}

// null means "not set" (JSON has no undefined), so it renders exactly like an absent field; any other non-array throws.
function checkSources(p) {
  const skills = p.skills == null ? [] : p.skills
  const standards = p.standards == null ? [] : p.standards
  if (!Array.isArray(skills)) throw new Error(`piece "${p.title}": skills must be an array`)
  const badSkill = skills.findIndex(path => typeof path !== 'string' || !path.startsWith('/') || !path.endsWith('/SKILL.md'))
  if (badSkill !== -1) throw new Error(`piece "${p.title}": skills entry ${showEntry(skills[badSkill])} is not an absolute path ending in /SKILL.md`)
  if (!Array.isArray(standards)) throw new Error(`piece "${p.title}": standards must be an array`)
  const badId = standards.findIndex(id => typeof id !== 'string' || !/^S-\d{2}$/.test(id))
  if (badId !== -1) throw new Error(`piece "${p.title}": standards entry ${showEntry(standards[badId])} is not an S-nn ID (e.g. S-01)`)
  if (standards.length && (typeof p.worktree !== 'string' || !p.worktree.startsWith('/')))
    throw new Error(`piece "${p.title}": standards need an absolute worktree path, got ${showEntry(p.worktree)}`)
  return { skills, standards }
}

function implementPrompt(p, mustFix, earlierFix = null) {
  const { skills, standards } = checkSources(p)
  const sources = `${skills.length ? `\nRead these adapted skills before editing:\n${skills.map(path => `- ${path}`).join('\n')}` : ''}${standards.length ? `\nRead ${p.worktree}/CODING_STANDARDS.md before editing; apply only these IDs: ${standards.join(', ')}.` : ''}`
  const fix = earlierFix !== null
    ? `\n\nThis is a FIX round. Sol (original seat: ${implOf(p)}) failed this piece twice; opus-seat is the escalation. Independent checks found these must-fix items; fix every one, change nothing else:\nround 0 (claimed fixed; context):\n- ${earlierFix.join('\n- ')}\nround 1 (fix these now):\n- ${mustFix.join('\n- ')}`
    : mustFix && mustFix.length ? `\n\nThis is a FIX round. Independent checks found these must-fix items; fix every one, change nothing else:\n- ${mustFix.join('\n- ')}` : ''
  return `[class:implement]
${LAUNCH}\n${rules(p)}

Piece: ${p.title}
${p.spec}${sources}${fix}${p.scenario != null ? `
User scenario: ${JSON.stringify(p.scenario)}. The fold runs this user scenario with scenario-run on the base and on your result; the head must pass. Never edit, skip or weaken it or any file it names.` : ''}

Check (must pass): \`${p.check}\`${proofLines(p).length ? `
Prove it works (pstack): after the check passes, run each of these from the worktree root, in order, and return every proof.json path from their evidence arrays in proofs. Each must exit 0; if one fails, fix the code, not the command:
${proofLines(p).map(c => `  ${c}`).join('\n')}` : ''}
If ${p.worktree}/apps/vpd exists and this piece changes what a product's UI shows, and you took verification screenshots of it, stage the final shots for the dashboard's Review tab: \`python3 -m apps.vpd stage screen --unit <product>/<story>/<slug> --title "<surface>" --shot desktop-light=<png> [--shot phone-dark=<png> ...]\` (${p.worktree}/docs/wiki/vpd-review.md). Stage nothing for a change no one can see; the command itself skips a product this branch did not touch.
${bumpStep(p)}When the check passes, commit your change in the worktree with the message: ${p.commit_message || p.title}
Co-Authored-By trailer as your instructions require. If your instructions forbid commits, leave it staged and say so.
Report: done, git diff --stat against the branch base, the last 30 lines of the check output, the commit sha (or 'staged'), and every deviation from the spec with its reason.
${INFRA}`
}

function runPrompt(p, _impl, seat = 'codex-test-runner') {
  return `[class:test-run]
${LAUNCH}\nPR: lab-${slug(p)} (no GitHub PR yet; use this string wherever your procedure says <pr>).
Head: run \`${SNAPSHOT} ${p.worktree}\` first: it prints a sha of a commit object holding the worktree's current state, committed or not, and touches no ref or file. Check out and test that sha, and return it as snapshot.
Eval command: ${p.check}
If the eval dies before any check runs because the Codex sandbox forbids something the check needs on this host (for example Postgres initdb: "could not create shared memory segment: Operation not permitted"), rerun the same command at the same snapshot with your own Bash tool, outside Codex, append that output to out_path under a line saying so, and report that exit code.
Return out_path (the absolute path of the captured output file), exit_code, tail and snapshot. A failing eval is a result, not an infra_error; an eval that could not run is infra, as below.${seat === 'codex-test-runner' ? '' : `\nYou stand in for codex-test-runner, which returned nothing. Never edit, commit or stage anything in ${p.worktree}. Run the eval in a disposable worktree: git -C ${p.worktree} worktree add --detach "$TMPDIR/run-<first 8 of the snapshot>" <snapshot>, cd there, run the eval command with its output captured to /Volumes/StudioExt/repos/factory-worktrees/.verify-runs/run-<first 8 of the snapshot>-standin.txt (mkdir -p the directory first), then git -C ${p.worktree} worktree remove --force that disposable worktree. out_path is that output file.`}
${INFRA}`
}

// Verify default (E7, 2026-09-26): a fresh cross-vendor verifier per piece and round, then the release-facts gate on every
// piece. Money path adds a risk lens and runs the release lens beside it; any one lens failing blocks, and the fix round gets
// every lens's must-fix items. No standing reviewers.
const RISK = `LENS: RISK ONLY. Ignore style and ordinary correctness; another reviewer covers those. Judge only the money-path invariants: tenancy and RLS isolation, auth and consent (no bypass, no step-up skipped), idempotency (a retry or replay with the same key does not act or charge twice), receipts (every paid or state-changing act leaves one), durability (no lost write on crash or failed connect), and double charges. Construct the concrete input or sequence that breaks one, within this piece's behavior or as a regression of what works on the base, and say which line allows it; that is a must_fix. A break you cannot construct that concretely goes to advisory. Pass only if you tried and could not construct one.\n`
// Review calibration (2026-09-27, Nate's gate: docs/wiki/ci-and-gates.md "When the reviewer says FAIL", ADR 0089
// amendment 2026-09-26). An audit of 89 folds found 41% of must-fix items outside the piece's intent, 17 items the
// reviewer itself called advisory, and 90% of FAIL pieces landing anyway. must_fix is now concrete and intent-bound (or a
// concrete regression); everything else is advisory, which never fails a round or drives a fix, and the PR lists it.
// One shared rubric for every review lens and the final decider.
const bar = strict => `must_fix is only for: a concrete input, state or sequence under which the diff breaks what the piece's spec says, or misses a spec item, cited with file:line; a concrete regression (something that works on the base breaks with this diff; name the input and file:line); a change outside the allowed files; an edited acceptance test; a test that would still pass with its behavior removed; a check or proof command that fails; a dishonest test or scenario (a tautology, a mock of the unit under test, reading source instead of running it, a skip, or special-casing the scenario's inputs); a scenario that fails on head. A UI finding (layout, styling, copy or what a screen shows) is a must_fix only with a failing page-shot you ran, cited by its image path; without one it is advisory. ${strict ? "This piece is on the money path or a one-way door: if you are unsure whether such a concrete defect is real, keep it in must_fix and say what would settle it." : "If you cannot name the concrete input, state, sequence or regression that shows a defect, you are unsure of it: put it in advisory with what would settle it, never in must_fix."} Everything else goes to advisory: hypotheticals past the bar the spec sets, more hardening, style, report wording or counts, and scope questions the spec already answers. Advisory never fails the piece and never drives a fix round; the PR lists it. pass is true exactly when must_fix is empty.`
// The release bump helper (scripts/ci/bump_release.py, agent-rails claim #1963) edits only these, audited against its
// plan() on 2026-09-27: exit 0 done, 3 manual steps remain and nothing was written, 1 it failed. A piece with
// bump: false skips the implement step and the scope exemption; the facts gate still runs the plan.
const bumpOn = p => p.bump !== false
const BUMP_FILES = `the version field of an app's manifest.json or definition/definition.json; a new migrations/<from>__<to>.json copy under that app (agents/*/migrations/*.json), and removal of the branch copy it renamed`
const bumpStep = p => bumpOn(p) ? `Release bump: if scripts/ci/bump_release.py exists in the worktree, after the check passes and before committing, run \`${PY} scripts/ci/bump_release.py --apply --base ${p.base || 'origin/main'}\` from the worktree root. What it writes is in scope even when not listed above: ${BUMP_FILES}. Exit 0: if it wrote anything, rerun the check, and put its output in your report. Exit 3: it wrote nothing and lists manual steps; do the ones inside this piece's files and run it again, and report the rest as deviations. Exit 1: report its error as a deviation.
` : ''
// Args: worktree, base, snapshot. Diffs the snapshot against its merge-base with the base itself. Every failure prints
// "unknown: ..." (counted as a match, S-01): a missing base ref or snapshot, a failed diff, a globs file the base lists
// but git cannot read, and any exception (bad JSON, a non-string glob) through sys.excepthook. Only a base whose tree
// has no globs file falls back to the two policy files.
// Matches the changed files against config/money-path-paths.json read from the base, plus the two policy files, with
// fnmatch.fnmatchcase, as scripts/queue_pr.py does. The policy files count even when the base has no globs file (a PR
// that adds one). Prints the matches, or a line starting with "none".
const MONEY_PY = `import fnmatch,json,subprocess,sys;sys.excepthook=lambda t,e,tb:print("unknown: "+t.__name__+": "+str(e)[:120]+"; treat as money path");w,b,s=(sys.argv[1:]+["","",""])[:3];run=lambda *c:subprocess.run(["git","-C",w,*c],capture_output=True,text=True);m=run("merge-base",b,"HEAD");d=run("diff","--no-renames","--name-only",m.stdout.strip(),s) if m.returncode==0 and s else None;t=run("ls-tree","--name-only",b,"--","config/money-path-paths.json");r=run("show",b+":config/money-path-paths.json") if t.returncode==0 and t.stdout.strip() else None;bad="no snapshot" if not s else "merge-base "+b+" HEAD failed" if m.returncode else "git diff failed" if d.returncode else "cannot list "+b if t.returncode else "cannot read config/money-path-paths.json at "+b if r is not None and r.returncode else None;g=[] if bad else (json.loads(r.stdout)["globs"] if r else [])+["config/money-path-paths.json","config/verdict-authors.json"];x=[] if bad else [f for f in d.stdout.splitlines() if any(fnmatch.fnmatchcase(f,y) for y in g)];print("unknown: "+bad+"; treat as money path" if bad else ", ".join(x) or ("none" if r else "none (no config/money-path-paths.json at "+b+")"))`
// What the lane does with each terminal status (docs/wiki/ci-and-gates.md "When the reviewer says FAIL"; ADR 0089
// amendment 2026-09-26). A ruling binds to the tree the decider judged: any change after it needs a fresh review.
const POST_FAIL = `push the branch, open the PR, post this FAIL on its current diff (python3 scripts/queue_pr.py verdict <pr> --fail --reviewer '<reviewer>' --author-vendor <author_vendor> --notes-file <a file listing must_fix and, when same_vendor_review is true, "same-vendor (Codex empty)">; include that phrase in the PR comment too)`
const bound = d => `The ruling holds only for tree ${d.tree} (HEAD ${d.head}): check git -C <worktree> rev-parse HEAD^{tree} before you push and again before the override; if it differs, the code changed after the ruling, so rerun the review instead of overriding.`
const FOUNDER_RULE = `Money path (a changed file matches config/money-path-paths.json on the base branch, or the PR edits it or config/verdict-authors.json) or a PR marked Merge danger: one-way needs a founder's yes: add --founder aneym|shiniken --founder-evidence <url or unblock id>. queue_pr.py judges that on the whole PR, so money_path_files here is a hint, not a pass.`
const nextOverride = d => `The fold's decider ruled every open finding outside the piece's review bar (override.items), so land it by override: ${POST_FAIL}, then python3 scripts/queue_pr.py override <pr> --reason "<override.reason>" --by opus-seat, then python3 scripts/queue_pr.py queue <pr>. ${bound(d)} ${FOUNDER_RULE} If queue_pr.py asks for a founder anyway, file founder with unblock_file as is and add --founder <login> --founder-evidence <unblock id> on a yes. Do not start a new fold on the same findings.`
const nextFounder = (d, why) => `A founder decides this piece: ${why} File founder with unblock_file as is (plain words, one choice), keep working on other pieces, and collect the answer with unblock_check. To land it on a yes: ${POST_FAIL}, then python3 scripts/queue_pr.py override <pr> --reason "<override.reason>" --by opus-seat --founder <login> --founder-evidence <unblock id>, then python3 scripts/queue_pr.py queue <pr>. ${bound(d)} On any other answer, follow it. Do not start a new fold on the same findings.`
// cause is the infra of the call that blocked the piece; an earlier call that recovered on a retry says nothing about it.
const nextInfra = cause => cause.some(l => l.endsWith(NOT_DONE))
  ? `A fix by the other vendor from the code's author did not finish (${cause.slice(-3).join('; ')}), so the code has no single author and no review could be cross-vendor to it. Finish the open findings in the worktree, then rerun the fold for this piece. Do not queue it and do not override.`
  : cause.some(l => l.includes(CANT_RUN))
  ? `The piece's check command could not run twice (${cause.slice(-3).join('; ')}). The check is malformed, not the code: fix the check command in the piece spec and rerun the fold for this piece. No verdict was given; do not queue it and do not override.`
  : `Tooling failed and no verdict was given on the current code (${cause.slice(-3).join('; ')}). This is ours to fix, not the piece's: repair the runner, sandbox or seat, then rerun the fold for this piece. Do not queue it and do not override.`
const joinNext = (...lines) => lines.filter(Boolean).join(' ')
// facts.money_path_files is the matcher's line copied verbatim. Anything but a line starting "none" counts as a match (S-01).
const moneyHit = facts => !/^none\b/i.test(String(facts.money_path_files ?? '').trim())
function moneyFlag(p, facts) {
  if (!facts || typeof facts.money_path_files !== 'string') return {}
  if (p.money_path && !moneyHit(facts))
    return { notes: [`money_path was set, but no changed file matches config/money-path-paths.json: the flag is stricter than the gate (${facts.money_path_files}).`] }
  if (!p.money_path && moneyHit(facts)) {
    const line = `Changed files match the money path (${facts.money_path_files || '(empty report)'}), but the piece ran without money_path, so the any-fail risk panel never reviewed it.`
    return { notes: [line], queue_allowed: false, next: `${line} Rerun the fold with money_path: true; do not queue on this result.` }
  }
  return {}
}
function conflictPrompt(p, report) {
  return `[class:implement]\n${LAUNCH}\n${rules(p)}\nThe release carry could not rebase onto ${p.base || 'origin/main'} because these files conflicted: ${report.conflict_files.join(', ')}. Rebase onto ${p.base || 'origin/main'}, resolve those files keeping both the piece's intended behavior and main's changes, run the piece check \`${p.check}\`, and commit. Never stash and never push. Return the normal RESULT shape.\n${INFRA}`
}
function migrationPrompt(p, report) {
  return `[class:implement]\n${LAUNCH}\nWorktree: ${p.worktree}. Work only there. Never stash, never push. The release bump requires a storage migration:\n${report.bump.manual.join('\n')}\nWrite the storage migration steps bump_release asks for under the app's migrations/ as bump_release expects, run the piece check \`${p.check}\`, and commit. Return the normal RESULT shape.\n${INFRA}`
}
const trainOf = carry => carry?.status === 'ok' && typeof carry?.bump?.skipped === 'string' && carry.bump.skipped.startsWith('train ') ? carry.bump.skipped.slice(6).trim() || null : null
function carrySummary(carry, base) {
  if (carry?.status !== 'ok') return ''
  const runs = carry.generators.map(g => `${g.argv.join(' ')} (exit ${g.exit})`).join(', ') || 'none'
  const name = trainOf(carry)
  return `Release carry this round: base ${base}, head ${carry.head}, rebased ${carry.rebased}, generators ${runs}, bump exit ${carry.bump.exit}. Review the resulting tree and report any remaining defect as must_fix.${name ? ` This piece is on the ${name} version train (factory config/trains.json): its version bump is made at landing by the train, which rebases, runs bump_release and reviews again, so a missing version bump or a code-floor version-drift finding for an app on that train is expected here and is not a defect; put it in advisory at most. Version reuse, and drift in any app outside the train, still count.` : ''}`
}
function verifyPrompt(p, impl, run, lens, authors, strict = false, sc = null, sameVendorFallback = false, carry = null) {
  const evidence = run ? run.by && run.by.startsWith('box ')
    ? `\nThe suite was run by ${run.by}; its captured output is at ${run.out_path} (exit ${run.exit_code}). Read that log; it ran on the worktree state whose commit is ${run.snapshot}. Do not rerun the full check; run single tests only to probe a finding.`
    : `\nThe suite was run by ${run.by || 'codex-test-runner'}; its captured output is at ${run.out_path} (exit ${run.exit_code}). Read that file; do not claim a run you did not see.`
    : `\nRerun the check \`${p.check}\` yourself.`
  return `[class:verify]
${LAUNCH}\n${sameVendorFallback ? 'Same-vendor fallback: Codex is exhausted; you are a fresh Anthropic reviewer of Anthropic-authored code. Judge independently.\n' : ''}${lens === 'risk' ? RISK : ''}Independent verification. Another agent (${authorsLine(authors)}) implemented the piece below in ${p.worktree}; it may be uncommitted by design. Try to REFUTE that it is done and correct. ${bar(strict)}\n${carrySummary(carry, p.base || 'origin/main')}
Read-only: never edit files, never push, never write to GitHub.
Piece: ${p.title}
Spec:
${p.spec}
Files it was allowed to touch: ${p.files.join(', ')}. Any change outside them is a must-fix${bumpOn(p) ? `, except a release bump by scripts/ci/bump_release.py, which may touch only: ${BUMP_FILES}. Any other edit to those files is still out of scope` : ''}.${p.acceptance_tests && p.acceptance_tests.length ? `\nAcceptance tests it must not have edited: ${p.acceptance_tests.join(', ')}. Any edit to them is a must-fix.` : ''}
Implementer's report:
${JSON.stringify(impl)}
${evidence}${proofLines(p).length ? `
Proof: rerun these from ${p.worktree} yourself (they drive the real product through the verify CLI); any non-zero exit, or a proofs list that does not match what you saw, is a must-fix:
${proofLines(p).map(c => `  ${c}`).join('\n')}` : ''}${p.scenario != null ? `
Scenario spec (written by Opus; the implementer may not edit it): ${JSON.stringify(p.scenario)}
The fold ran it with scenario-run (ran_on ${sc.ran_on}${sc.ran_on === 'box' ? ` ${sc.box}, attempt ${sc.attempt}` : ''}) on base ${sc.base_sha} and head ${sc.head_sha}: verdict ${sc.verdict}.
Result JSON: ${clip(JSON.stringify(sc.result), 4000)}
Result path: ${sc.result_path}
Each side's stdout.txt and stderr.txt are under ${sc.out}/base and ${sc.out}/head; read them. Judge the scenario's honesty against the rubric: does it test the ask from the user's side, and does base fail for the right reason. A still_fails result is already a must_fix in the fold; do not repeat it.` : ''}
Do: read the full diff (git -C ${p.worktree} diff and log since ${p.base || 'origin/main'}), and look for wrong behavior, missed spec items, tests that restate the implementation instead of testing behavior, and${p.money_path ? ' (MONEY PATH) tenancy leaks, auth/consent bypass, non-idempotent retries, missing receipts, and' : ''} scope creep.
Return pass, must_fix (concrete, one line each; empty when pass), advisory (one line each; may be empty), notes, check_reran (the last lines of the run you read or did) and check_exit (that run's exit code).${strict ? '' : `
patch (optional): if a must_fix item's fix is 20 changed lines or fewer inside the allowed files, return that fix as a unified diff that git apply accepts from the worktree root (a/ and b/ paths). The fold applies it as this piece's one fix round, then reruns the check, the scenario, the release facts and a fresh review on the final diff. Leave it out when unsure or when the fix is larger.`}
${INFRA}`
}

function factsPrompt(p, carry = null) {
  const base = p.base || 'origin/main'
  const snap = `$(${SNAPSHOT} ${p.worktree})`
  const revert = p.acceptance_tests && p.acceptance_tests.length
    ? `if ${p.worktree}/scripts/ci/fails_on_base.py exists: cd ${p.worktree} && ${PY} scripts/ci/fails_on_base.py ${p.acceptance_tests.join(' ')} --base $(git -C ${p.worktree} merge-base ${base} HEAD) --also-passes-here --python ${PY} --json   (must report FAILS-ON-BASE; PASSES-ON-BASE, STILL-FAILS-HERE or BROKEN is a must-fix). Otherwise: ~/.agent-rails/orch-lab/bin/revert-check.sh ${p.worktree} ${base} '${p.check.replace(/'/g, `'\\''`)}' ${p.acceptance_tests.join(' ')}   (must print REVERT-FAILS; REVERT-PASSES is a must-fix: the test does not catch the bug)`
    : 'no acceptance tests named: write "n/a"'
  return `[class:verify]
${LAUNCH}\nRelease-facts gate for the piece "${p.title}" in ${p.worktree}. This lens checks release facts only (code floor, versions, migrations, deploy config, revert check). It is not the code review: a separate verifier reviews the code (cross-vendor unless Codex is exhausted), so the vendor-separation rule does not apply here and is never a reason to fail. Read-only on the worktree: never edit, commit, push or write to GitHub.
${carrySummary(carry, base)}\nA diff review cannot see these misses, so check each one against the merged tree, not the diff text:
1. Code floor: if ${p.worktree}/scripts/ci/code_floor.py exists: cd ${p.worktree} && ${PY} scripts/ci/code_floor.py --base $(git -C ${p.worktree} merge-base ${base} HEAD) --head ${snap}
   (snapshot.sh makes a commit object of the worktree state, committed or not, and touches no ref or file; without it an uncommitted change is invisible to the floor.)
   (it reports compile errors, boot-import failures, version drift: an app whose admitted bytes change at the same version, and version reuse). Any finding is a must-fix. If it exists but cannot run, say exactly why; that is a fail, not a pass.
   If the repo has no scripts/ci/code_floor.py (agent-lb, factory and art have none), do the floor by hand on the worktree as it stands, with the repo's own interpreter (${p.worktree}/.venv/bin/python if it exists, else \`uv run --frozen python\` from ${p.worktree}):
   - Python: byte-compile every .py file the diff touches, import each changed module, and import the app's boot module too (the module its service starts: app.main in agent-lb; otherwise the module a pyproject [project.scripts] entry, Procfile, Dockerfile or launchd plist runs), so a symbol a changed module dropped fails here when an unchanged importer still needs it.
   - JS or TS inside a package (a package.json at or above the file, below the worktree root): run that package's own type check or build.
   - JS with no package: node --check each touched .js/.mjs/.cjs file. A workflow script (it starts with export const meta, and uses top-level await and return) fails node --check by design; parse it instead as an async function body: node -e 'const s=require("fs").readFileSync(process.argv[1],"utf8").replace(/^export const meta/m,"const meta");new (Object.getPrototypeOf(async function(){}).constructor)(s)' <file>
   Name exactly what you ran and its exit code. Any error is a must-fix; a missing code_floor.py alone is not. If the diff touches code and none of these checks ran, that is a must-fix too: say which file had no check.
2. Versions: ${trainOf(carry) ? `For apps on train \`${trainOf(carry)}\` (their paths are listed under that train in ${HOME_DIR}/factory/config/trains.json), a planned bump or migration from bump_release is n/a: do not set bump_needed or report it as a must_fix, and do not apply the rest of this item's bump wording to those apps. For other apps only: ` : ''}if ${p.worktree}/scripts/ci/bump_release.py exists: cd ${p.worktree} && ${PY} scripts/ci/bump_release.py --json --base ${base}   (it plans the bumps the code floor requires and writes nothing without --apply). ${bumpOn(p) ? `Exit 0 with bumps or migrations in its plan means the piece left out a bump the helper can make: that is not a must-fix. Set bump_needed true, name each planned entry in versions, and do not count the code floor's version-drift finding for that same app as a must-fix in item 1 either; the fold runs the helper and reruns this gate. Exit 3 (manual entries) is a must-fix naming each manual entry; exit 1 means it could not run: say why, that is a fail.` : `A non-empty plan (any bumps, migrations or manual entries, or exit 3) is a must-fix that names each entry; exit 1 means it could not run: say why, that is a fail.`} Otherwise, by hand: if the diff changes an app or agent package's code or catalog (agents/<name>/, apps/), is its manifest version bumped, and are the files that pin or re-admit it consistent? A missing bump found by hand is a must-fix.
3. Migrations: does any change to schema, stored shapes or table DDL ship with a migration and an upgrade path? Say n/a when nothing stored changed.
4. Deploy config and packaging: if pyproject.toml, Dockerfile*, .railwayignore, railway*.json/toml, Procfile or scripts/hosted_stack.py changed, does the Railway image still build and boot (e.g. pyproject package lists must not name directories .railwayignore drops; #1440 broke staging that way)? Say n/a when none changed.
5. Revert check: ${revert}
6. Money-path files (information for the orchestrator, never a must-fix and never a reason to fail): run
   ${PY} -c '${MONEY_PY}' ${p.worktree} ${base} ${snap}
   and copy its one output line into money_path_files exactly. If it cannot run, write why, not a line starting "none".
Return pass (true only if items 1-5 are clean or n/a; bump_needed alone does not fail it), bump_needed (only as item 2 says), must_fix (one line each), one line per item 1-5, and money_path_files.
${INFRA}`
}

function bumpPrompt(p, facts) {
  return `[class:implement]
${LAUNCH}\nWorktree: ${p.worktree}. Work only there. Never git stash, never push, never write to GitHub.
The release-facts gate found a bump this piece left out: ${clip(facts.versions, 600)}.
From the worktree root run \`${PY} scripts/ci/bump_release.py --apply --base ${p.base || 'origin/main'}\` and nothing else. Edit no file by hand; the helper may write only: ${BUMP_FILES}.
If the piece's own change is committed and the tree was clean before the helper ran, commit what it wrote with message "Release bump"; otherwise leave it uncommitted.
Report in the RESULT shape: done true only if the helper exited 0; check_output_tail is its last 30 lines; commit is the sha or 'uncommitted'; deviations is the exit code and error when nonzero. Include diffstat.
${INFRA}`
}

// The decider (2026-09-27; p6's notes): it judges each open finding against bar, the review bar in models.md, so a
// finding outside the piece's intent becomes an override with a reason, never another round. It records the HEAD and
// tree it judged, and writes the founder ask in plain words, ready for unblock_file.
function decidePrompt(p, { items, rounds, authors, gateWhy }) {
  const history = rounds.map((r, i) => `round ${i} (code by ${authors[i]}): ${r.must_fix.join(' | ')}`).join('\n')
  return `[class:decide]
${LAUNCH}
You are the fold's decider for the piece below, in ${p.worktree}. Independent reviewers failed it in ${rounds.length} rounds in a row (code by ${[...new Set(authors)].join(', ')}). You make the call an orchestrator would have made, and the lane acts on it without asking anyone else, so judge carefully. Read-only: never edit, commit, push or write to GitHub.
Piece: ${p.title}
Spec:
${p.spec}
Files it was allowed to touch: ${p.files.join(', ')}.
Money path or one-way: ${gateWhy || 'no'}.
The review bar (Nate's gate, models.md 2026-09-27): ${bar(!!gateWhy)}
So a finding belongs in must_fix only when it is a concrete defect against this piece's spec or intent, a concrete regression, or a hard rule (a change outside the allowed files, an edited acceptance test, a test that passes with its behavior removed, a failing check or proof). A finding outside that bar is never a reason for another round: rule it disagree, and the fold overrides it with your reason.
Open findings from the last round. Judge each against the bar and against the code: read the diff (git -C ${p.worktree} diff and log since ${p.base || 'origin/main'}), and rerun the check \`${p.check}\` or a narrower command when a finding turns on behavior:
${items.map((m, i) => `${i + 1}. ${m}`).join('\n')}
Earlier rounds, so you can see what keeps coming back:
${history}
For each finding return { index (its number above), ruling, reason }. agree: a real defect inside the bar that must be fixed before this lands. disagree: outside the bar (outside the intent, a hypothetical past what the spec asks, more hardening, style, wording), or wrong about the code; the reason says which and cites file:line or the output that shows it. A [release] finding is a failing release check (code floor, versions) and a [check] finding is the check's exit code in the test run or the reviewer's rerun: both always count as agreed, so guide the fix. A [scenario] finding (the scenario still fails on head) also always counts as agreed. ${gateWhy ? "This piece is on the money path or a one-way door: when you are unsure whether a concrete defect inside the bar is real, agree." : 'When you are unsure whether a finding is a real defect inside the bar (no one has shown its failing input or regression), rule it disagree with a reason starting "unsure:" that says what would settle it; the override lists it for the PR.'}
This is the last decision: the fold makes no more fixes. An agreed finding splits the piece unless a money-path, one-way or product decision needs a founder. Give guidance for the split.
one_way: true if landing this is a one-way door (a mass or outbound send to real people, an irreversible migration or backfill, data deletion or overwrite, a secret or permission change, a paid or external side effect); one_way_reason says why.
needs_founder: true only when a finding turns on a product or taste call the spec does not settle.
founder: fill it when a founder must decide: a money-path or one-way change with unresolved findings, or needs_founder. Otherwise it is optional. Write it for a founder who has not seen this work, in plain words: no seat names, lens tags, file paths, commands or jargon unless they need one to decide. title: a question under 90 characters. why: under 1200 characters, saying what the piece does, what the reviewers flagged, what you ruled and why, and what happens on each choice. question: the one thing to decide. choices: 2-4 short options (for example "Land it as is", "Park it"). recommend: one of the choices; recommend_why: under 200 characters.
head and tree: run git -C ${p.worktree} rev-parse HEAD, then ${SNAPSHOT} ${p.worktree} and git -C ${p.worktree} rev-parse <that sha>^{tree}. Return both full shas. They bind your ruling to the code you judged; a later change voids it.
${INFRA}`
}

const FACTORY = (args && args.factory_bin) || `${HOME_DIR}/.local/bin/factory`
// Box seats have no ~/.agents on the jobs user (2026-09-28, 4 of 6 pieces infra_blocked: "test-audit/SKILL.md is absent
// on this box"). job submit --with rsyncs the skill into the attempt and rewrites every ~/.agents/skills/test-audit
// spelling in the prompt to the staged copy, the same way extra check directories travel.
const TEST_AUDIT_DIR = `${HOME_DIR}/.agents/skills/test-audit`
const withDirs = (p, extra = []) => [...new Set([...(p.with || []), ...extra])].map(dir => ` --with ${shellQuote(dir)}`).join('')
const BOX_SUBMIT = { type: 'object', properties: { items: { type: 'array', items: { type: 'object', properties: { key: { type: 'string' }, exit_code: { type: 'integer' }, printed: { type: 'string' } }, required: ['key', 'exit_code', 'printed'] } } }, required: ['items'] }
const BOX_WAIT = { type: 'object', properties: { lines: { type: 'array', items: { type: 'string' } } }, required: ['lines'] }
const FLOOR_RELAY = { type: 'object', properties: { exit_code: { type: 'integer' }, printed: { type: 'string' }, infra_error: { type: 'string' } }, required: ['exit_code', 'printed'] }
const boxRelayPrompt = body => `[class:relay]
${LAUNCH}
Your task: use the Write and Bash tools as directed below. Do not edit, fix or judge the unit's code yourself.
${body}`
const BOX_RUN_ID = { type: 'object', properties: { line: { type: 'string' } }, required: ['line'] }
let runIdPromise
const runId = () => {
  if (runIdPromise) return runIdPromise
  if (typeof args.run_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(args.run_id)) return (runIdPromise = Promise.resolve(args.run_id))
  runIdPromise = (async () => {
    for (let tries = 0; tries < 3; tries++) {
      try {
        const answer = await agent(boxRelayPrompt(`Run /usr/bin/python3 -c 'import uuid; print(uuid.uuid4().hex[:12])' with Bash and return its printed line as {line: <string>}.`), { label: `box-run-id:${tries + 1}`, phase: 'Implement', schema: BOX_RUN_ID, agentType: 'host-relay' })
        if (/^[0-9a-f]{12}$/.test(answer && answer.line)) return answer.line
      } catch (error) { log(`box run id: ${String(error)}`) }
    }
    return null
  })()
  return runIdPromise
}
const ranOn = new Map()
const markRun = (p, label, where, seat, extra = {}) => ranOn.get(p).push({ round: label, where, seat, ...extra })
const boxEligible = p => !!(p.host && (p.repo || args.repo) && !p.proof)
const terminalBox = cause => ({ infra_error: cause, box_terminal: true })
// Both worktree roots lanes use (agent-rails-wt and agent-rails-worktrees); the second was missed before 2026-09-28.
const railsWorktree = w => typeof w === 'string' && (w === '/Volumes/StudioExt/repos/agent-rails' || /^\/Volumes\/StudioExt\/repos\/agent-rails-(wt|worktrees)\//.test(w))
const checkSetting = p => args.hosts === 'studio' ? 'studio' : p.checks || args.checks || (!railsWorktree(p.worktree) ? 'studio' : p.host === 'spill' ? 'spill' : 'box')
// 2026-09-28 (Alex: "push our factory to be utilizing parallelization when possible", not PC first): 'spill' keeps a round
// on Studio while it has room; job submit --spill takes a box only when Studio is loaded, else refuses with studio_has_room.
const boxTarget = host => !host || host === 'spill' ? 'auto' : host
const checkPath = (p, text = p.check) => {
  const home = path => path.replace(/^~\//, `${HOME_DIR}/`).replace(/^\$HOME\//, `${HOME_DIR}/`)
  const inside = (path, dir) => path === dir || path.startsWith(dir.replace(/\/$/, '') + '/')
  const dirs = [p.worktree, ...(p.with || [])].map(home)
  const paths = String(text).match(/(?:~\/|\$HOME\/|\/Users\/|\/Volumes\/|\/private\/|\/tmp\/|\/var\/|\/opt\/)[^\s'"`;|&()<>{}]+/g) || []
  return paths.find(path => /(?:^|\/)\.\.(?:\/|$)/.test(home(path)) || !dirs.some(dir => inside(home(path), dir)) && !['~/rails-ci/venv/bin/python', '$HOME/rails-ci/venv/bin/python', `${HOME_DIR}/rails-ci/venv/bin/python`].includes(path))
}
const boxKey = (unit, id, tag) => {
  const suffix = `${unit}:${id}:${tag}`
  const lane = String(args.lane || 'fold').replace(/[^A-Za-z0-9._:-]/g, '-').replace(/^[^A-Za-z0-9]+/, '') || 'fold'
  return `${lane.slice(0, 199 - suffix.length)}:${suffix}`
}
let submitIndex = 0
let waitIndex = 0
async function boxRound(p, prompt, opts, seat) {
  const label = opts.label
  const tag = label.split(':', 1)[0]
  const studio = async reason => {
    markRun(p, label, 'studio', seat, { host: LOCAL_HOST })
    const out = await agent(prompt, { ...opts, agentType: seat })
    return out && { ...out, deviations: [out.deviations, reason].filter(Boolean).join('; ') }
  }
  const id = await runId()
  if (!id) return studio('box: no run id')
  const unit = unitOf(p)
  const file = `${HOME_DIR}/.agent-rails/orch-lab/boxes/fold/${id}/${unit}/${tag}.md`
  const key = boxKey(unit, id, tag)
  const command = `FACTORY_WORKFLOW=${shellQuote(`fold:${args.lane || 'fold'}:${id}`)} ${shellQuote(FACTORY)} job submit --unit ${shellQuote(unit)} --repo ${shellQuote(p.repo || args.repo)} --worktree ${shellQuote(p.worktree)} --prompt ${shellQuote(file)} --check ${shellQuote(p.check)} --files ${shellQuote(p.files.join(','))} --seat ${shellQuote(seat)} --class implement --key ${shellQuote(key)} --box ${shellQuote(boxTarget(p.host))}${p.host === 'spill' ? ' --spill' : ''}${withDirs(p, [TEST_AUDIT_DIR])}`
  let attemptId = ''
  let box = boxTarget(p.host)
  for (let tries = 0; tries < 3; tries++) {
    let item, line
    try {
      const body = `Create the directory for ${file}. Write only the lines between these markers to ${file} using the Write tool (not a shell heredoc); the marker lines are not part of the file:\n<<<PROMPT ${label}\n${prompt}\nPROMPT ${label}>>>\nRun \`${command}\` with a 600000 ms Bash timeout. Record its exit code and the one JSON line it printed. Return {items: [{key: ${JSON.stringify(label)}, exit_code: <number>, printed: <JSON line as a string>}]}.`
      const answer = await agent(boxRelayPrompt(body), { label: `box-submit:${++submitIndex}`, phase: opts.phase, schema: BOX_SUBMIT, agentType: 'host-relay' })
      item = answer && Array.isArray(answer.items) && answer.items.find(x => x.key === label)
      line = JSON.parse(item.printed)
    } catch (error) { log(`box submit ${key}: ${String(error)}`) }
    if (item && item.exit_code === 0 && line && typeof line.attempt === 'string') {
      attemptId = line.attempt
      box = line.box || box
      markRun(p, label, 'box', seat, { box, attempt: attemptId })
      break
    }
    if (item && item.exit_code === 6) {
      const id = line && line.attempt || key
      markRun(p, label, 'box', seat, { box: line && line.box || box, attempt: id })
      return terminalBox(`box launch_unknown ${id}: not rerun`)
    }
    if (item && [2, 3, 5].includes(item.exit_code)) {
      if (item.exit_code === 3 && line && line.reason === 'studio_has_room') return studio('')
      const reason = line ? `${line.error || `exit ${item.exit_code}`} (${line.reason || 'none'})` : `exit ${item.exit_code}`
      return studio(`box ${reason}: ran on Studio`)
    }
  }
  if (!attemptId) return terminalBox(`box submit ${key}: no answer after three tries; not run on Studio`)
  let strikes = 0
  while (true) {
    let answer
    try {
      answer = await agent(boxRelayPrompt(`Run ${shellQuote(FACTORY)} job wait --any ${shellQuote(attemptId)} --timeout 240 --brief (Bash timeout 600000 ms). Return every printed JSON line verbatim in lines, including a running line; return an empty array if nothing printed.`), { label: `box-wait:${++waitIndex}`, phase: opts.phase, schema: BOX_WAIT, agentType: 'host-relay' })
    } catch (error) { log(`box wait ${attemptId}: ${String(error)}`) }
    const parsed = (answer && Array.isArray(answer.lines) ? answer.lines : []).map(x => { try { return JSON.parse(x) } catch (_) { return null } }).filter(Boolean)
    const result = parsed.find(x => x.attempt === attemptId)
    if (!result) {
      if (parsed.some(x => x.status === 'running')) { strikes = 0; continue }
      if (++strikes < 3) continue
      return terminalBox(`box wait ${attemptId}: no answer after three strikes`)
    }
    if (result.outcome === 'no_result') return studio(`box ${box}: no_result (${result.reason || 'none'}): ran on Studio`)
    if (result.outcome !== 'applied') return terminalBox(`box ${attemptId}: ${result.reason || result.status || result.outcome || 'unknown result'}`)
    const good = result.status === 'done' && result.seat_rc === 0 && result.check_rc === 0 && result.done !== false
    // A seat that exited non-zero on the box (a 429 or usage limit, a crash) with nothing applied did not answer: it is
    // infra, so attempt() retries it and then re-seats it (Sol to sonnet-implementer, then opus-seat). Once its edits are applied they
    // are its work, so the piece stays with that author and goes to review.
    const seatFailed = !good && !result.diffstat && Number.isInteger(result.seat_rc) && result.seat_rc !== 0
    return { done: good, ...(seatFailed ? { infra_error: `box ${result.box || box}: seat exit ${result.seat_rc} (${result.status})` } : {}), diffstat: result.diffstat || '', check_output_tail: `box check ${result.check_rc === 0 ? 'passed' : 'failed'}; log: ${result.record || ''}/check.log`, commit: 'uncommitted', deviations: good ? `ran on box ${result.box || box} (attempt ${attemptId})` : `box ${result.box || box}: seat exit ${result.seat_rc == null ? 'null' : result.seat_rc}, check exit ${result.check_rc} (${result.status})` }
  }
}

async function boxJob(p, label, phase, name, checkArg, pre = '') {
  const id = await runId()
  if (!id) return { reason: 'box: no run id' }
  const unit = unitOf(p)
  const file = `${HOME_DIR}/.agent-rails/orch-lab/boxes/fold/${id}/${unit}/${name}.md`
  const key = boxKey(unit, id, name)
  const command = `${pre}FACTORY_WORKFLOW=${shellQuote(`fold:${args.lane || 'fold'}:${id}`)} ${shellQuote(FACTORY)} job submit --unit ${shellQuote(unit)} --repo ${shellQuote(p.repo || args.repo || 'agent-rails')} --worktree ${shellQuote(p.worktree)} --prompt ${shellQuote(file)} --check ${checkArg} --files ${shellQuote(p.files.join(','))} --seat-cmd true --no-apply --key ${shellQuote(key)} --box ${shellQuote(boxTarget(p.host))}${checkSetting(p) === 'spill' ? ' --spill' : ''}${withDirs(p)}`
  let attemptId = ''
  let box = boxTarget(p.host)
  for (let tries = 0; tries < 3; tries++) {
    let item, line
    try {
      const body = `Create the directory for ${file}. Write exactly one line to ${file} using the Write tool (not a shell heredoc):\n${name === 'run' || name.startsWith('run-') || name.startsWith('floor') ? 'check-only' : 'scenario-only'} run for ${p.title}\nRun \`${command}\` with a 600000 ms Bash timeout. Record its exit code and the one JSON line it printed. Return {items: [{key: ${JSON.stringify(label)}, exit_code: <number>, printed: <JSON line as a string>}]}.`
      const answer = await agent(boxRelayPrompt(body), { label: `box-submit:${++submitIndex}`, phase, schema: BOX_SUBMIT, agentType: 'host-relay' })
      item = answer && Array.isArray(answer.items) && answer.items.find(x => x.key === label)
      line = JSON.parse(item.printed)
    } catch (error) { log(`box check submit ${key}: ${String(error)}`) }
    if (item && item.exit_code === 0 && line && typeof line.attempt === 'string') {
      attemptId = line.attempt
      box = line.box || box
      break
    }
    if (item && [2, 3, 5, 6].includes(item.exit_code)) return { reason: `box ${line ? `${line.error || `exit ${item.exit_code}`} (${line.reason || 'none'})` : `exit ${item.exit_code}`}: ran on Studio` }
  }
  if (!attemptId) return { reason: `box submit ${key}: no answer after three tries; ran on Studio` }
  let strikes = 0
  while (true) {
    let answer
    try {
      answer = await agent(boxRelayPrompt(`Run ${shellQuote(FACTORY)} job wait --any ${shellQuote(attemptId)} --timeout 240 (Bash timeout 600000 ms). Return every printed JSON line verbatim in lines, including a running line; return an empty array if nothing printed.`), { label: `box-wait:${++waitIndex}`, phase, schema: BOX_WAIT, agentType: 'host-relay' })
    } catch (error) { log(`box check wait ${attemptId}: ${String(error)}`) }
    const parsed = (answer && Array.isArray(answer.lines) ? answer.lines : []).map(x => { try { return JSON.parse(x) } catch (_) { return null } }).filter(Boolean)
    const result = parsed.find(x => x.attempt === attemptId)
    if (!result) {
      if (parsed.some(x => x.status === 'running')) { strikes = 0; continue }
      if (++strikes < 3) continue
      return { reason: `box wait ${attemptId}: no answer after three strikes; ran on Studio` }
    }
    if (result.outcome !== 'checked' || result.status !== 'done' || !Number.isInteger(result.check_rc) || typeof result.base !== 'string' || typeof result.record !== 'string' || typeof result.check_tail !== 'string')
      return { reason: `box ${attemptId}: ${result.reason || result.status || result.outcome || 'unusable result'}; ran on Studio` }
    return { result, box: result.box || box, attempt: attemptId }
  }
}

async function boxCheck(p, label, phase) {
  const tag = label.match(/^run(-r\d+)?:/)?.[1] || ''
  const checked = await boxJob(p, label, phase, 'run' + tag, shellQuote(p.check))
  if (checked.reason) return checked
  const { result, box, attempt } = checked
  if (result.check_rc !== 0) return { reason: `box check exit ${result.check_rc}: confirming on Studio`, boxExit: result.check_rc }
  return { run: { out_path: `${result.record}/check.log`, exit_code: result.check_rc, tail: clip(result.check_tail, 3000), snapshot: result.base, by: `box ${box} (attempt ${attempt})` }, box, attempt }
}

// Classify declared files against the merge-base's money-path policy, before choosing the review panel.
// Run isolated from the worktree so a piece's Python modules cannot shadow the stdlib.
const MONEY_PREFLIGHT_PY = `import fnmatch,json,subprocess,sys
root,base,files=sys.argv[1],sys.argv[2],json.loads(sys.argv[3])
mb=subprocess.check_output(['git','-C',root,'merge-base',base,'HEAD'],text=True).strip()
policy='config/money-path-paths.json'
tree=subprocess.check_output(['git','-C',root,'ls-tree','--name-only',mb,'--',policy],text=True).strip()
globs=json.loads(subprocess.check_output(['git','-C',root,'show',f'{mb}:{policy}'],text=True))['globs'] if tree else []
if not isinstance(globs,list) or any(not isinstance(glob,str) for glob in globs):
    raise ValueError('money-path globs must be a list of strings')
globs+=['config/money-path-paths.json','config/verdict-authors.json']
print(json.dumps([file for file in files if any(fnmatch.fnmatchcase(file,glob) for glob in globs)]))`

// Discover the base's floor, never a floor definition edited by the piece. Only runnable files on head count.
const FLOOR_FILES_PY = `import importlib.util,json,os,subprocess,sys,tempfile
from pathlib import Path
root=Path(sys.argv[1])
base=sys.argv[2]
mb=subprocess.check_output(['git','-C',str(root),'merge-base',base,'HEAD'],text=True).strip()
def show(path):
    return subprocess.check_output(['git','-C',str(root),'show',f'{mb}:{path}'])
with tempfile.TemporaryDirectory(prefix='money-floor-') as temp:
    directory=Path(temp)
    os.chdir(directory)
    sys.path.insert(0,str(directory))
    script=directory/'floor_audit.py'
    script.write_bytes(show('scripts/ci/floor_audit.py'))
    doc=directory/'money-path-floor.md'
    doc.write_bytes(show('docs/prd/lean-ci-2026-09/context/money-path-floor.md'))
    spec=importlib.util.spec_from_file_location('floor_audit',script)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sections=module.floor_sections(doc)
    candidates={file for files in sections.values() for file in files}
    head=set(subprocess.check_output(['git','-C',str(root),'ls-tree','-r','--name-only','HEAD'],text=True).splitlines())
    print(json.dumps(sorted(candidates & head)))`
const tailLines = text => String(text || '').trimEnd().split('\n').slice(-40).join('\n')
const floorNodes = text => {
  const lines = String(text || '').split('\n')
  const nodes = lines.filter(line => /^(?:FAILED|ERROR)\s+\S+/.test(line)).map(line => line.split(/\s+/)[1])
  return nodes.slice(0, 3).join(', ') || tailLines(text).split('\n').find(line => /(?:FAILED|ERROR|failed|error)/.test(line)) || 'see floor output'
}
const floorSummary = text => {
  const line = String(text || '').split('\n').reverse().find(s => /^(?:=+ )?\d+ (?:passed|failed|skipped|errors?|warnings?|deselected|xfailed|xpassed)\b.*\bin (?:\d+(?:\.\d+)?s|\d+:\d{2}:\d{2})(?:\s|=|$)/.test(s)) || ''
  const time = line.match(/\bin (\d+(?:\.\d+)?s|\d+:\d{2}:\d{2})(?:\s|=|$)/)
  const seconds = time ? time[1].endsWith('s') ? Number(time[1].slice(0, -1)) : time[1].split(':').reduce((sum, part) => sum * 60 + Number(part), 0) : null
  const tests = { passed: 0, failed: 0, skipped: 0, errors: 0 }
  if (time) for (const [, count, kind] of line.slice(0, time.index).matchAll(/(\d+) (passed|failed|skipped|errors?)\b/g))
    tests[kind === 'error' ? 'errors' : kind] += Number(count)
  return { seconds, tests: time ? tests : null }
}
async function moneyPreflight(p) {
  if (p.money_path != null) return { source: 'piece' }
  const command = `tmp=$(mktemp -d) || exit 97; (cd "$tmp" && ${shellQuote(PY)} -I -c ${shellQuote(MONEY_PREFLIGHT_PY)} ${shellQuote(p.worktree)} ${shellQuote(p.base || 'origin/main')} ${shellQuote(JSON.stringify(p.files))}); rc=$?; rm -rf "$tmp"; exit "$rc"`
  const infra = []
  for (let n = 1; n <= 2; n++) {
    const response = await attempt('host-relay', () => relayPrompt(`Run exactly this ONE Bash command with a 600000 ms timeout:\n${command}\nReturn exit_code and printed (the full stdout and stderr).`),
      { label: `money-preflight${n === 2 ? '-retry' : ''}:${p.title}`.slice(0, 60), phase: 'Implement', schema: FLOOR_RELAY, agentType: 'host-relay' },
      { once: true, ok: out => !out.infra_error && Number.isInteger(out.exit_code) && typeof out.printed === 'string' })
    infra.push(...response.infra)
    let matches
    try { matches = response.out?.exit_code === 0 ? JSON.parse(response.out.printed.trim()) : null } catch { matches = null }
    if (Array.isArray(matches) && matches.every(f => typeof f === 'string' && p.files.includes(f))) {
      p.money_path = matches.length > 0
      return { source: matches.length ? 'preflight' : 'preflight-none' }
    }
    infra.push(`money preflight exit ${response.out?.exit_code ?? 'unknown'}: ${clip(response.out?.printed, 300) || 'invalid matches'}`)
  }
  return { infra }
}
async function moneyFloor(p, label, phase) {
  let inventoryAttempts = 0
  const record = (verdict, details = {}) => {
    const summary = floorSummary(details.output_tail)
    return { mode: MONEY_FLOOR_MODE, verdict, ran_on: details.ran_on || 'studio', ...((details.ran_on || 'studio') === 'studio' ? { host: LOCAL_HOST } : {}),
      box: details.box || null, files: details.files || 0, exit: details.exit ?? null,
      seconds: summary.seconds ?? (typeof details.seconds === 'number' && Number.isFinite(details.seconds) && details.seconds >= 0 ? details.seconds : null),
      tests: summary.tests, attempts: details.attempts || inventoryAttempts || 1,
      output_tail: tailLines(details.output_tail), ...(details.reason ? { reason: details.reason } : {}) }
  }
  const discover = `${shellQuote(PY)} -I -c ${shellQuote(FLOOR_FILES_PY)} ${shellQuote(p.worktree)} ${shellQuote(p.base || 'origin/main')}`
  let files, inventory
  for (let n = 1; n <= 2; n++) {
    inventoryAttempts = n
    inventory = await attempt('host-relay', () => relayPrompt(`Run exactly this ONE Bash command with a 600000 ms timeout:\n${discover}\nReturn exit_code and printed (the full stdout and stderr).`),
      { label: label.replace(/^floor(-r\d+)?:/, (_, round) => `floor-files${round || ''}${n === 2 ? '-retry' : ''}:`), phase, schema: FLOOR_RELAY, agentType: 'host-relay' },
      { ok: out => !out.infra_error && Number.isInteger(out.exit_code) && typeof out.printed === 'string' })
    try { files = inventory.out?.exit_code === 0 ? JSON.parse(inventory.out.printed.trim()) : null } catch { files = null }
    if (Array.isArray(files) && files.length && files.every(f => typeof f === 'string' && /^tests\/[^\s'"`$;]+\.py$/.test(f))) break
  }
  if (!Array.isArray(files) || !files.length || files.some(f => typeof f !== 'string' || !/^tests\/[^\s'"`$;]+\.py$/.test(f)))
    return record('infra', { attempts: inventoryAttempts, reason: inventory.infra.join('; ') || `floor inventory exit ${inventory.out?.exit_code ?? 'unknown'}: missing or empty: ${clip(inventory.out?.printed, 300) || 'no files'}`, output_tail: inventory.out?.printed })
  const command = `${shellQuote(PY)} -m pytest -q -p no:cacheprovider ${files.map(shellQuote).join(' ')}`
  const floorPiece = { ...p, check: command }
  let last
  for (let n = 1; n <= 2; n++) {
    let ran_on = 'studio', box = null, exit = null, output_tail = '', reason = ''
    if (checkSetting(floorPiece) !== 'studio') {
      const badPath = checkPath(floorPiece)
      const checked = badPath ? { reason: `check_paths: ${badPath}` } : await boxJob(floorPiece, label.replace(/^floor(-r\d+)?:/, (_, round) => `floor-box${round || ''}:`), phase,
        `floor${label.match(/-r\d+:/)?.[0].slice(0, -1) || ''}-a${n}`, shellQuote(command))
      if (checked.result) {
        ran_on = 'box'
        box = checked.box
        exit = checked.result.check_rc
        output_tail = checked.result.check_tail
        markRun(p, label, 'box', 'floor', { box, attempt: checked.attempt })
        if (exit === 0 || exit === 1) return record(exit === 0 ? 'pass' : 'fail', { ran_on, box, files: files.length, exit, output_tail, seconds: [checked.result.seconds?.check, checked.result.seconds?.total].find(value => typeof value === 'number' && Number.isFinite(value) && value >= 0), attempts: inventoryAttempts + n - 1 })
        reason = `box floor exit ${exit}`
      } else reason = checked.reason
    }
    // A box refusal, timeout or harness error is retried on Studio, never interpreted as a failing pytest assertion.
    const studio = `cd ${shellQuote(p.worktree)} || { echo "floor exit 97"; exit 97; }; o=$(mktemp) || { echo "floor exit 97"; exit 97; }; ${command} > "$o" 2>&1; rc=$?; tail -n 40 "$o"; rm -f "$o"; echo "floor exit $rc"`
    const local = await attempt('host-relay', () => relayPrompt(`Run exactly this ONE Bash command with a 600000 ms timeout:\n${studio}\nReturn exit_code (the number from the floor exit line) and printed (the test output, without that exit line).`),
      { label: label.replace(/^floor(-r\d+)?:/, (_, round) => `${n === 1 ? 'floor-studio' : 'floor-retry'}${round || ''}:`), phase, schema: FLOOR_RELAY, agentType: 'host-relay' },
      { ok: out => !out.infra_error && Number.isInteger(out.exit_code) && typeof out.printed === 'string' })
    ran_on = 'studio'
    markRun(p, label, 'studio', 'floor', { host: LOCAL_HOST, ...(reason ? { reason } : {}) })
    exit = local.out?.exit_code ?? null
    output_tail = local.out?.printed || output_tail
    if (exit === 0 || exit === 1 || exit === 2) return record(exit === 0 ? 'pass' : 'fail', { ran_on, box, files: files.length, exit, output_tail, attempts: inventoryAttempts + n - 1 })
    last = record('infra', { ran_on, box, files: files.length, exit, output_tail, attempts: inventoryAttempts + n - 1,
      reason: [reason, ...local.infra, exit === null ? 'no floor result' : `pytest exit ${exit}`].filter(Boolean).join('; ') })
  }
  return last
}

async function boxScenario(p, label, phase) {
  const tag = label.match(/^scenario(-r\d+)?:/)?.[1] || ''
  const pre = `b=$(git -C ${shellQuote(p.worktree)} merge-base ${shellQuote(p.base || 'origin/main')} HEAD) && `
  const before = `printf '%s\\n' ${shellQuote(JSON.stringify(p.scenario))} > "$FACTORY_OUT/scenario.spec.json" && "$FACTORY_BOX_HOME/bin/scenario-run" --piece ${shellQuote(pieceId(p))} --base `
  const after = ` --head HEAD --spec "$FACTORY_OUT/scenario.spec.json" --out "$FACTORY_OUT/scenario" --repo "$PWD"`
  const checked = await boxJob(p, label, phase, 'scenario' + tag, shellQuote(before) + '"$b"' + shellQuote(after), pre)
  if (checked.reason) return checked
  const { result, box, attempt } = checked
  let line
  for (const candidate of result.check_tail.split('\n').reverse()) {
    try {
      const value = JSON.parse(candidate)
      if (value && typeof value === 'object' && !Array.isArray(value) && typeof value.verdict === 'string') { line = candidate; break }
    } catch (_) { /* not a JSON result line */ }
  }
  const parsed = line && parseScenario({ line, runner_exit: result.check_rc })
  if (!parsed) return { reason: `box ${attempt}: scenario result unreadable; ran on Studio` }
  if (parsed.verdict === 'infra') return { reason: `box infra ${parsed.infra_reason}: ran on Studio`, infra: `scenario: box infra ${parsed.infra_reason} (${attempt})` }
  if (typeof result.out !== 'string' || result.out_skipped) return { reason: `box ${attempt}: scenario outputs not collected (${result.out_skipped || 'no out'}); ran on Studio` }
  const out = result.out + '/scenario'
  return { sc: { verdict: parsed.verdict, infra_reason: parsed.infra_reason, base_sha: parsed.base.sha,
    head_sha: parsed.head.sha, head_outcome: parsed.head.outcome, head_exit: parsed.head.exit,
    out, result_path: out + '/result.json', runs: 1, result: { ...parsed, out, box_out: parsed.out },
    ran_on: 'box', box: result.box || box, attempt } }
}

// One seat call under the infra rule: nothing back, a throw, an infra_error, or an answer ok() rejects is not an answer. The
// seat gets two tries, then its stand-in one. make(seat) builds the prompt (the vendor guard throws there) before
// each call.
async function attempt(seat, make, opts, { alt = ALT[seat], ok = out => !out.infra_error, piece = null, once = false } = {}) {
  const infra = []
  const tries = once ? [seat] : alt === ALT[seat] && IMPL_FALLBACK[seat] ? [seat, seat, ...IMPL_FALLBACK[seat]]
    : alt ? [seat, seat, alt] : [seat, seat]
  for (let i = 0; i < tries.length; i++) {
    const label = i ? opts.label.replace(/^([^:]*?)(-r\d+)?:/, (_, head, r) => `${head}-${i === 1 ? 'retry' : i === 2 ? 'reseat' : `reseat${i - 1}`}${r || ''}:`) : opts.label
    const prompt = make(tries[i])
    const callOpts = { ...opts, label: label.slice(0, 60), agentType: tries[i] }
    let out = null
    let thrown = ''
    try {
      if (piece && boxEligible(piece)) out = await boxRound(piece, prompt, callOpts, tries[i])
      else {
        if (piece) markRun(piece, callOpts.label, 'studio', tries[i], { host: LOCAL_HOST })
        out = await agent(prompt, callOpts)
      }
    } catch (error) { thrown = `threw: ${clip((error && error.message) || error, 160)}` }
    if (out && !cantRun(out) && ok(out, tries[i])) return { out, seat: tries[i], infra }
    const checkError = out && cantRun(out) ? (String(out.infra_error || '').trim().toLowerCase().startsWith(CANT_RUN)
      ? out.infra_error : `${CANT_RUN}: exit ${out.exit_code ?? out.check_exit}`) : ''
    infra.push(`${opts.label.split(':')[0]} on ${tries[i]}: ${checkError || thrown || (!out ? 'returned nothing' : out.infra_error || (out.done === false ? NOT_DONE : 'unusable answer'))}`)
    if (checkError && i >= 1) break
    if (out && out.box_terminal) break
  }
  return { out: null, seat, infra }
}

// A fix by the other vendor from the code's last writer must report done. A partial or empty edit would leave code with
// no single author, and no reviewer could be cross-vendor to it, so done=false there is not an answer: it is retried,
// re-seated, then infra_blocked. A same-vendor fix keeps today's rule (its answer is reviewed either way).
const NOT_DONE = 'reported done=false'
const fixDone = author => (out, seat) => !out.infra_error && (vendorOf(seat) === vendorOf(author) || out.done === true)
const clip = (text, n) => { const t = String(text || '').trim(); return t.length > n ? `${t.slice(0, n - 3)}...` : t }
// An unblock_file payload the lane files as is (purpose decision, one must-decide choice).
function founderAsk(p, d, rulings, authors, why) {
  const f = d.founder || {}
  const given = (Array.isArray(f.choices) ? f.choices : []).map(c => clip(c, 80)).filter(Boolean).slice(0, 4)
  const choices = given.length >= 2 ? given : ['Land it as is', 'Park it', 'I will give direction']
  return {
    title: clip(f.title || `Land "${p.title}" as is?`, 90),
    why: clip(f.why || `${why} Open findings: ${rulings.map(r => r.item).join('; ')}`, 1200),
    only_you: 'judgment', purpose: 'decision', ...(args.lane ? { project: clip(args.lane, 64) } : {}),
    fields: [
      { name: 'decision', type: 'choice', label: clip(f.question || 'What should happen to this piece?', 200), choices, must_decide: true, required: true,
        ...(choices.includes(f.recommend) ? { recommend: { value: f.recommend, why: clip(f.recommend_why || 'The fold decider recommends it.', 200) } } : {}) },
      { name: 'note', type: 'text', label: 'Anything the fold should know (optional)', multiline: true },
    ],
    tried: [
      `The fold ran ${authors.length} review rounds with fixes by ${[...new Set(authors)].join(', ')}, and independent reviewers still fail it.`,
      `The fold's decider ruled on each open finding: ${rulings.map((r, i) => `${i + 1}) ${r.ruling}: ${r.reason}`).join(' ')}`,
    ].map(t => clip(t, 400)),
  }
}

// The release-facts verdict fails closed the same way as a lens: its must_fix items count even beside pass=true.
const factsFixOf = facts => !facts ? [] : [
  ...(facts.must_fix || []).map(m => `[release] ${m}`),
  ...(!facts.pass && !(facts.must_fix || []).length ? [`[release] The release-facts gate returned pass=false with no must_fix item; code floor: ${facts.code_floor}; versions: ${facts.versions}`] : []),
  ...(facts.bump_needed ? [`[release] a version bump is still needed: ${facts.versions}`] : []),
]
const SHA = /^[0-9a-f]{40,64}$/

async function foldPiece(p) {
  const rounds = []
  const authors = []       // authors[i]: the seat whose code round i reviewed
  const infra = []
  const decisions = []
  const deviations = []
  let previousFix = []
  let patchedBy = null
  let gateWhy = p.money_path ? 'yes, a money-path piece' : p.one_way ? 'yes, marked one-way' : ''
  const ownerBoundary = p.scenario == null ? 'owner-boundary: ' + p.owner_boundary.trim() : null
  const pieceRepo = p.repo || args.repo || 'agent-rails'
  let floorSkip = null
  let current
  let resultMeta = { escalated: false, fix_seats: [], scenario: ownerBoundary }
  let mechanicsReviewUsed = false
  let repeatReview = false
  ranOn.set(p, [])
  const releaseCarry = async (label, phase) => {
    const binary = shellQuote(`${HOME_DIR}/factory/bin/fold-carry`)
    const command = `if test -x ${binary}; then ${binary} --worktree ${shellQuote(p.worktree)} --base ${shellQuote(p.base || 'origin/main')} --python ${shellQuote(PY)}${args.carry_generators !== undefined ? ` --generators ${shellQuote(JSON.stringify(args.carry_generators))}` : ''} --json; echo "fold-carry exit $?"; else printf '%s\\n' '${JSON.stringify({ status: 'skipped', behind: 0, rebased: false, head: '', conflict_files: [], generators: [], generated_files: [], bump: { exit: null, changed: [], manual: [] }, floor: { exit: null, tail: '' }, reason: 'fold-carry not installed on host' })}'; echo "fold-carry exit 0"; fi`
    const call = await attempt('host-relay', () => relayPrompt(`Run exactly this ONE Bash command with a 600000 ms timeout:\n${command}\nReturn line (the single JSON report line verbatim) and runner_exit (the number from the fold-carry exit line).`),
      { label, phase, schema: CARRY_RELAY, agentType: 'host-relay' }, { ok: out => !out.infra_error && !!parseCarry(out) })
    infra.push(...call.infra)
    return call.out ? parseCarry(call.out) : { status: 'infra', reason: call.infra.join('; ') || 'unparseable carry report' }
  }
  const carryRound = async (round, author, label, phase) => {
    if (args.release_carry === 'off') return null
    let report = await releaseCarry(label, phase)
    for (let n = 0; n < 2; n++) {
      if (report.status === 'conflict' && n === 0) {
        const fix = await attempt(author, () => conflictPrompt(p, report), { label: `carry-conflict-r${round}:${p.title}`.slice(0, 60), phase, schema: RESULT }, { piece: p })
        infra.push(...fix.infra)
        if (!fix.out) return { status: 'infra', reason: fix.infra.join('; ') }
        authors[round] = fix.seat
        current = fix.out
        rounds[round] = { ...(rounds[round] || {}), carry_conflict_round: { seat: fix.seat, result: fix.out, files: report.conflict_files } }
      } else if (report.status === 'needs_migration' && n === 0) {
        const fix = await attempt('opus-seat', () => migrationPrompt(p, report), { label: `carry-migration-r${round}:${p.title}`.slice(0, 60), phase, schema: RESULT }, { piece: p })
        infra.push(...fix.infra)
        if (!fix.out) return { status: 'infra', reason: 'storage-shape change needs a migration the fold could not write' }
        authors[round] = fix.seat
        current = fix.out
        gateWhy ||= 'yes, the release carry wrote a storage migration'
        rounds[round] = { ...(rounds[round] || {}), carry_migration_round: { seat: fix.seat, result: fix.out, manual: report.bump.manual } }
      } else if (report.status === 'infra' && n === 0) {
        report = await releaseCarry(`${label.replace(':', '-retry:')}`, phase)
        break
      } else break
      report = await releaseCarry(`${label.replace(':', '-after-fix:')}`, phase)
    }
    return report
  }
  const ended = (status, latest, extra = {}) => ({ title: p.title, worktree: p.worktree, status, latest_verdict: latest, queue_allowed: false, ran_on: ranOn.get(p),
    rounds, ...(deviations.length ? { deviations } : {}), ...(decisions.length ? { decisions } : {}), ...(infra.length ? { infra } : {}), ...extra, ...resultMeta,
    next: joinNext(resultMeta.next, extra.next) })
  const blocked = (latest, cause) => ended('infra_blocked', latest, { next: nextInfra(cause) })
  const preflight = await moneyPreflight(p)
  if (preflight.infra) { infra.push(...preflight.infra); return blocked('NONE', preflight.infra) }
  resultMeta.money_path_source = preflight.source
  // Computed after the preflight: it is what sets p.money_path for a piece that did not declare it.
  floorSkip = p.money_path && pieceRepo !== 'agent-rails' ? `money floor skipped: repo ${pieceRepo} is not agent-rails` : null
  if (floorSkip) resultMeta.notes = [floorSkip]
  if (p.money_path) gateWhy = 'yes, a money-path piece'

  const first = await attempt(implOf(p), () => implementPrompt(p), { label: `impl:${p.title}`.slice(0, 60), phase: 'Implement', schema: RESULT }, { piece: p })
  infra.push(...first.infra)
  if (!first.out) return blocked('NONE', first.infra)
  current = first.out
  authors.push(first.seat)

  for (let round = 0; ; round++) {
    const ph = round === 0 ? 'Verify' : 'Fix'
    const tag = round ? '-r' + round : ''
    const lbl = prefix => `${prefix}${tag}:${p.title}`.slice(0, 60)
    const carry = await carryRound(round, authors[round], lbl(repeatReview ? 'carry-final-review' : 'carry'), ph)
    repeatReview = false
    if (carry && !['ok', 'skipped'].includes(carry.status)) {
      const detail = carry.status === 'needs_migration' ? 'storage-shape change needs a migration the fold could not write' : `${carry.status} ${carry.reason || carry.conflict_files?.join(', ') || ''}`
      return blocked('NONE', [`carry: ${detail}`])
    }
    if (carry) rounds[round] = { ...(rounds[round] || {}), carry: { ...carry, generators: carry.generators.map(g => ({ ...g, tail: undefined })), floor: carry.floor } }
    const author = authors[round]
    const reviewer = reviewSeatFor(author)
    const escalated = authors.some(s => vendorOf(s) !== vendorOf(implOf(p)))
    // author_vendor is the vendor of the code this round's reviewer judged, so a verdict posted with it names the
    // real author after an escalation; original_author_vendor keeps the first implementer's.
    resultMeta = { escalated, fix_seats: [...authors], scenario: ownerBoundary, money_path_source: preflight.source, ...(floorSkip || carry?.status === 'skipped' ? { notes: [...(floorSkip ? [floorSkip] : []), ...(carry?.status === 'skipped' ? [`carry: skipped ${carry.reason}`] : [])] } : {}), ...(resultMeta.money_floor ? { money_floor: resultMeta.money_floor } : {}), ...(patchedBy ? { patched_by: patchedBy } : {}), reviewer, author_vendor: vendorOf(author), original_author_vendor: vendorOf(implOf(p)),
      ...(escalated ? { fix_vendor: vendorOf(author) } : {}) }
    // The check run (box or Studio) and the scenario run go in parallel.
    const runStage = async () => {
      let run = null
      let studioReason = ''
      let boxExit = null
      if (checkSetting(p) !== 'studio') {
        const badPath = checkPath(p)
        if (badPath) studioReason = `check_paths: ${badPath}`
        else {
          const checked = await boxCheck(p, lbl('run'), ph)
          if (checked.run) {
            run = checked.run
            markRun(p, lbl('run'), 'box', 'check', { box: checked.box, attempt: checked.attempt })
          } else {
            studioReason = checked.reason
            boxExit = checked.boxExit ?? null
          }
        }
      }
      if (!run && (studioReason || vendorOf(reviewer) === 'openai')) {
        if (studioReason) markRun(p, lbl('run'), 'studio', 'check', { reason: studioReason, host: LOCAL_HOST })
        if (vendorOf(reviewer) === 'openai') {
          const r = await attempt('codex-test-runner', seat => runPrompt(p, current, seat), { label: lbl('run'), phase: ph, schema: RUN })
          infra.push(...r.infra)
          if (!r.out) return { blocked: r.infra }
          run = r.out
          if (boxExit !== null && boxExit !== run.exit_code) deviations.push(`box check exit ${boxExit}, Studio exit ${run.exit_code}`)
        }
      }
      return { run, boxExit }
    }
    const scenarioCall = async () => {
      if (p.scenario == null) return null
      let studioReason = ''
      if (checkSetting(p) !== 'studio') {
        const badPath = checkPath(p, JSON.stringify(p.scenario))
        const boxed = badPath ? { reason: `scenario_paths: ${badPath}` } : await boxScenario(p, lbl('scenario'), ph)
        if (boxed.sc) {
          markRun(p, lbl('scenario'), 'box', 'scenario', { box: boxed.sc.box, attempt: boxed.sc.attempt })
          resultMeta.scenario = boxed.sc
          return { sc: boxed.sc }
        }
        if (boxed.infra) infra.push(boxed.infra)
        studioReason = boxed.reason
        markRun(p, lbl('scenario'), 'studio', 'scenario', { reason: studioReason, host: LOCAL_HOST })
      }
      for (let n = 1; n <= 2; n++) {
        const command = `d=${HOME_DIR}/.agent-rails/scenario-runs/${pieceId(p)}; mkdir -p "$d"; o="$d/$(date -u +%Y%m%dT%H%M%SZ)-$$-r${round}-a${n}"; printf '%s\\n' ${shellQuote(JSON.stringify(p.scenario))} > "$o.spec.json"; SCENARIO_PYTHON=${shellQuote(PY)} ${shellQuote(SCENARIO_RUN)} --piece ${shellQuote(pieceId(p))} --base "$(git -C ${shellQuote(p.worktree)} merge-base ${shellQuote(p.base || 'origin/main')} HEAD)" --head "$(${shellQuote(SNAPSHOT)} ${shellQuote(p.worktree)})" --spec "$o.spec.json" --out "$o" --repo ${shellQuote(p.worktree)}; echo "scenario-run exit $?"`
        const call = await attempt('host-relay', () => relayPrompt(`Run exactly this ONE Bash command with a 600000 ms timeout:\n${command}\nReturn line (the single JSON line scenario-run printed, verbatim) and runner_exit (the number from the scenario-run exit line).`),
          { label: lbl(n === 1 ? 'scenario' : 'scenario-rerun'), phase: ph, schema: SCENARIO_RELAY, agentType: 'host-relay' },
          { ok: out => !out.infra_error && !!parseScenario(out) })
        infra.push(...call.infra)
        if (!call.out) return { blocked: call.infra }
        const result = parseScenario(call.out)
        const sc = { verdict: result.verdict, infra_reason: result.infra_reason, base_sha: result.base.sha,
          head_sha: result.head.sha, head_outcome: result.head.outcome, head_exit: result.head.exit,
          out: result.out, result_path: result.out ? result.out + '/result.json' : null, runs: n, result,
          ran_on: 'studio', host: LOCAL_HOST, ...(studioReason ? { reason: studioReason } : {}) }
        resultMeta.scenario = sc
        if (sc.verdict !== 'infra') return { sc }
        infra.push(`scenario: infra ${sc.infra_reason} (${sc.out || 'no output dir'})`)
      }
      return { blocked: infra.filter(line => line.startsWith('scenario: infra ')) }
    }
    const [runResult, scenarioResult, floor] = await Promise.all([runStage(), scenarioCall(),
      p.money_path && pieceRepo === 'agent-rails' && MONEY_FLOOR_MODE !== 'off' ? moneyFloor(p, lbl('floor'), ph) : null])
    if (floor) resultMeta.money_floor = floor
    if (floor?.verdict === 'infra' && MONEY_FLOOR_MODE === 'block') {
      const line = `money_floor: infra ${floor.reason || 'no result'}`
      infra.push(line)
      return blocked('NONE', [line])
    }
    if (scenarioResult && scenarioResult.blocked) return blocked('NONE', scenarioResult.blocked)
    if (runResult.blocked) return blocked('NONE', runResult.blocked)
    const { run, boxExit } = runResult
    const sc = scenarioResult ? scenarioResult.sc : null
    const factsCall = (prefix = 'facts') => attempt('verifier', () => factsPrompt(p, carry), { label: lbl(prefix), phase: ph, schema: FACTS, effort: p.money_path ? 'xhigh' : 'high' })
    const early = p.money_path ? factsCall() : null
    const lenses = lensesOf(p)
    const rs = await Promise.all(lenses.map(async lens => {
      const opts = {
        label: lbl(lens === 'correct' ? (escalated && reviewer === 'codex-verifier' ? 'verify-codex' : 'verify') : 'lens-' + lens),
        phase: ph, schema: VERDICT, effort: p.money_path ? 'xhigh' : 'high',
      }
      const review = (seat, sameVendorFallback = false) => {
        assertCross(seat, author, sameVendorFallback)
        return verifyPrompt(p, current, run && (run.by || vendorOf(seat) === 'openai') ? run : null, lens, authors, !!gateWhy, sc, sameVendorFallback, carry)
      }
      const first = await attempt(reviewer, seat => review(seat), opts)
      if (first.out || reviewer !== 'codex-verifier' || !first.infra.some(line => line.includes(' on sol-consult:'))) return first
      const fallback = await attempt('verifier', seat => review(seat, true), { ...opts, label: opts.label.replace(/^([^:]*?)(-r\d+)?:/, (_, head, r) => `${head}-fallback${r || ''}:`) }, { once: true })
      return { ...fallback, infra: [...first.infra, ...fallback.infra], ...(fallback.out ? { same_vendor_review: true } : {}) }
    }))
    rs.forEach(r => infra.push(...r.infra))
    if (rs.some(r => !r.out)) {
      if (early) infra.push(...(await early).infra)
      return blocked('NONE', rs.filter(r => !r.out).flatMap(r => r.infra))
    }
    resultMeta.reviewer = [...new Set(rs.map(r => r.seat))].join(', ')
    if (rs.some(r => r.same_vendor_review)) resultMeta.same_vendor_review = true
    const vs = rs.map(r => {
      const x = r.out
      const dropped = trainOf(carry) ? (x.must_fix || []).filter(trainBumpItem) : []
      return dropped.length ? { ...x, must_fix: x.must_fix.filter(m => !trainBumpItem(m)),
        pass: dropped.length === x.must_fix.length || x.pass,
        advisory: [...(x.advisory || []), ...dropped.map(m => `[train] ${m}`)] } : x
    })
    const multi = lenses.length > 1
    const tagOf = i => multi ? `[${lenses[i]}${escalated ? '/' + rs[i].seat : ''}] ` : ''
    // A lens passes only when it says pass and names no must_fix item. An inconsistent verdict fails closed (S-01): its
    // must_fix items count even beside pass=true, and a bare pass=false carries its notes into the fix round.
    const lensFix = (x, i) => (x.must_fix || []).length ? x.must_fix.map(m => tagOf(i) + m)
      : x.pass ? [] : [`${tagOf(i)}The reviewer returned pass=false with no must_fix item; its notes: ${x.notes}`]
    // The check itself is a gate: a failing test run fails the round whatever the reviewers say. With no test run (an
    // Anthropic reviewer reruns the check itself), each reviewer's exit code is the run; a missing one counts as failing.
    const rerun = run ? -1 : vs.findIndex(x => x.check_exit !== 0)
    if (boxExit !== null && !run && rerun < 0) deviations.push(`box check exit ${boxExit}, Studio exit 0`)
    else if (boxExit !== null && !run && rerun >= 0 && Number.isInteger(vs[rerun].check_exit) && boxExit !== vs[rerun].check_exit)
      deviations.push(`box check exit ${boxExit}, Studio exit ${vs[rerun].check_exit}`)
    const checkFix = run ? (run.exit_code !== 0
      ? [`[check] The check \`${p.check}\` failed in the test run (exit ${run.exit_code}; output ${run.out_path}): ${clip(run.tail, 300)}`] : [])
      : rerun >= 0 ? [`[check] The check \`${p.check}\` failed when the reviewer reran it (exit ${vs[rerun].check_exit}): ${clip(vs[rerun].check_reran, 300)}`] : []
    const scenarioFix = sc && p.scenario?.type === 'pytest' && sc.verdict === 'passes_on_base'
      ? [`[scenario] The pytest scenario passes on base ${String(sc.base_sha).slice(0, 8)}, so it proves nothing about this change (result ${sc.result_path})`]
      : sc && (sc.verdict === 'still_fails' || sc.verdict === 'passes_on_base' && sc.head_outcome === 'fail')
        ? [`[scenario] The scenario still fails on head ${String(sc.head_sha).slice(0, 8)} (base ${String(sc.base_sha).slice(0, 8)}; head exit ${sc.head_exit}; result ${sc.result_path})`] : []
    const floorFix = floor?.verdict === 'fail' && MONEY_FLOOR_MODE === 'block'
      ? [floor.exit === 2 ? `money-path floor: tests failed to collect (exit 2): ${tailLines(floor.output_tail)}` : `money-path floor failed: ${floorNodes(floor.output_tail)}`] : []
    const reviewFix = vs.flatMap(lensFix)
    const v = {
      pass: !checkFix.length && !scenarioFix.length && !floorFix.length && !reviewFix.length,
      must_fix: [...checkFix, ...scenarioFix, ...floorFix, ...reviewFix],
      advisory: vs.flatMap((x, i) => (x.advisory || []).map(m => tagOf(i) + m)),
      notes: `${rs.some(r => r.same_vendor_review) ? 'same-vendor (Codex empty)\n' : ''}${vs.map((x, i) => tagOf(i) + x.notes).join('\n')}`,
      check_reran: vs[0].check_reran, lenses: multi ? vs : undefined,
    }
    // Facts run on a PASS, early on the money path, and before every decision (an override still needs clean release
    // facts, and the money-path line decides who may override).
    const fr = early ? await early : v.pass || round >= FIX_ROUNDS ? await factsCall() : null
    if (fr) {
      infra.push(...fr.infra)
      if (!fr.out) return blocked(v.pass ? 'NONE' : 'FAIL', fr.infra)
    }
    let facts = fr ? fr.out : null
    let bump = null
    if (facts && facts.bump_needed && bumpOn(p) && carry?.status !== 'ok') {
      const b = await attempt(author, () => bumpPrompt(p, facts), { label: lbl('bump'), phase: ph, schema: RESULT })
      infra.push(...b.infra)
      if (!b.out) return blocked(v.pass ? 'NONE' : 'FAIL', b.infra)
      bump = b.out
      const refreshed = await factsCall('facts-bump')
      infra.push(...refreshed.infra)
      if (!refreshed.out) return blocked(v.pass ? 'NONE' : 'FAIL', refreshed.infra)
      facts = refreshed.out
    }
    const droppedFacts = trainOf(carry) ? (facts?.must_fix || []).filter(trainBumpItem) : []
    const factsFix = factsFixOf(facts && trainOf(carry) ? { ...facts, bump_needed: false,
      must_fix: (facts.must_fix || []).filter(m => !trainBumpItem(m)),
      pass: droppedFacts.length > 0 && droppedFacts.length === facts.must_fix.length || facts.pass } : facts)
    v.advisory.push(...droppedFacts.map(m => `[release/train] ${m}`))
    // Carried on every return (resultMeta is spread last): the last round's advisory for the PR body, and the money-path
    // flag check, which can only lower queue_allowed.
    Object.assign(resultMeta, { advisory: v.advisory, notes: [...(carry?.status === 'skipped' ? [`carry: skipped ${carry.reason}`] : []), ...(rs.some(r => r.same_vendor_review) ? ['same-vendor (Codex empty): record this in queue_pr.py verdict --notes-file and the PR comment.'] : [])] }, moneyFlag(p, facts))
    if (floorSkip) resultMeta.notes.push(floorSkip)
    if (facts && moneyHit(facts) && !gateWhy) gateWhy = `yes, changed files match the money path (${facts.money_path_files || 'unreadable report'})`
    const mustFix = [...(v.pass ? [] : v.must_fix), ...factsFix]
    rounds[round] = { ...(rounds[round] || {}), impl: current, author, run, scenario: sc, ...(floor ? { money_floor: floor } : {}), verdict: v, facts, ...(bump ? { bump } : {}), must_fix: mustFix }
    if (v.pass && facts && !factsFix.length) {
      return { title: p.title, worktree: p.worktree, status: resultMeta.queue_allowed === false ? 'held' : 'pass', fix_rounds: round, latest_verdict: 'PASS', queue_allowed: true, ran_on: ranOn.get(p), rounds, final: current, verdict: v, facts,
        ...(deviations.length ? { deviations } : {}), ...(decisions.length ? { decisions } : {}), ...(infra.length ? { infra } : {}), ...resultMeta }
    }
    if (round < FIX_ROUNDS) {
      const candidate = !gateWhy && rs.find(r => typeof r.out.patch === 'string' && patchLines(r.out.patch) >= 1 && patchLines(r.out.patch) <= 20)
      if (candidate) {
        const patch = candidate.out.patch.endsWith('\n') ? candidate.out.patch : candidate.out.patch + '\n'
        const command = `f=$(mktemp "${'${TMPDIR:-/tmp}'}/fold-patch.XXXXXX"); printf '%s' ${shellQuote(patch)} > "$f"; git -C ${shellQuote(p.worktree)} apply --whitespace=nowarn "$f"; echo "git apply exit $?"; rm -f "$f"`
        const applied = await attempt('host-relay', () => relayPrompt(`Run exactly this ONE Bash command with a 600000 ms timeout:\n${command}\nReturn apply_exit (the number from the git apply exit line) and printed (the command output).`),
          { label: `patch-r${round + 1}:${p.title}`.slice(0, 60), phase: 'Fix', schema: PATCH_RELAY, agentType: 'host-relay' },
          { ok: out => !out.infra_error && Number.isInteger(out.apply_exit) })
        infra.push(...applied.infra)
        rounds[round].patch = { by: candidate.seat, lines: patchLines(patch), applied: applied.out?.apply_exit === 0,
          ...(!applied.out || applied.out.apply_exit !== 0 ? { printed: clip(applied.out?.printed || applied.infra.join('\n'), 300) } : {}) }
        if (applied.out?.apply_exit === 0) {
          patchedBy = candidate.seat
          resultMeta.patched_by = patchedBy
          current = { done: true, diffstat: 'reviewer patch', check_output_tail: '', commit: 'uncommitted', deviations: '' }
          authors.push(author)
          previousFix = mustFix
          continue
        }
      }
      // Before the last fix round: the author fixes; a Sol piece's second fix goes to opus-seat (escalation, E6/E10).
      const fixSeat = round === 1 && escalates(author) ? 'opus-seat' : author
      const escalating = fixSeat !== author && vendorOf(author) === 'openai'
      const f = await attempt(fixSeat, () => implementPrompt(p, mustFix, escalating ? previousFix : null), { label: `fix-r${round + 1}:${p.title}`.slice(0, 60), phase: 'Fix', schema: RESULT }, { ok: fixDone(author), piece: p })
      infra.push(...f.infra)
      if (!f.out) return blocked('FAIL', f.infra)
      current = f.out
      authors.push(f.seat)
      previousFix = mustFix
      continue
    }

    // After the last fix round: the decider rules on every open finding.
    const d = await attempt('opus-seat', () => decidePrompt(p, { items: mustFix, rounds, authors, gateWhy }), {
      label: `decide-r${round}:${p.title}`.slice(0, 60), phase: 'Decide', schema: DECISION, effort: gateWhy ? 'xhigh' : 'high',
    }, { ok: out => !out.infra_error && SHA.test(String(out.head || '').trim()) && SHA.test(String(out.tree || '').trim()) })
    infra.push(...d.infra)
    if (!d.out) return blocked('FAIL', d.infra)
    // Rulings fail closed: a finding with no ruling, a disagree with no reason, or more than one ruling counts as agreed.
    // Check, release, scenario and money-floor failures are measured gates; no ruling puts them outside the bar.
    const rulings = mustFix.map((item, i) => {
      const all = (Array.isArray(d.out.items) ? d.out.items : []).filter(x => x && x.index === i + 1)
      if (all.length > 1) return { item, ruling: 'agree', reason: `(ruled ${all.length} times; counted as agreed)` }
      const r = all[0]
      const reason = r ? String(r.reason || '').trim() : ''
      if (item.startsWith('[check] ') || item.startsWith('[release] ') || item.startsWith('[scenario] ') || item.startsWith('money-path floor failed: ') || item.startsWith('money-path floor: tests failed to collect (exit 2): ')) return { item, ruling: 'agree', reason: r && r.ruling === 'disagree' ? `(a failing check cannot be ruled outside the bar; counted as agreed) ${reason}`.trim() : reason || '(not ruled; counted as agreed)' }
      if (r && r.ruling === 'disagree' && reason) return { item, ruling: 'disagree', reason }
      return { item, ruling: 'agree', reason: !r ? '(not ruled; counted as agreed)' : r.ruling === 'disagree' ? '(disagreed with no reason; counted as agreed)' : reason }
    })
    const agreed = rulings.filter(r => r.ruling === 'agree')
    const disagreed = rulings.filter(r => r.ruling === 'disagree')
    const decision = { round, by: d.seat, rulings, guidance: String(d.out.guidance || ''), one_way: !!d.out.one_way,
      one_way_reason: String(d.out.one_way_reason || ''), needs_founder: !!d.out.needs_founder,
      head: String(d.out.head).trim(), tree: String(d.out.tree).trim() }
    decisions.push(decision)
    if (decision.one_way && !gateWhy) gateWhy = `yes, a one-way door (${decision.one_way_reason || 'the decider said so'})`

    const why = decision.needs_founder ? 'the decider found a product call the spec does not settle.'
      : agreed.length ? `the decider agrees ${agreed.length} open finding(s) are real defects, and the fold has no fixes left after ${round} fix rounds.`
      : `the decider ruled every open finding outside the review bar, but this change is on the money path or a one-way door (${gateWhy}), so an override needs a founder's yes.`
    const list = rs => rs.map((r, i) => `${i + 1}) ${r.item} -- ${r.reason}`).join(' ')
    const override = {
      reason: agreed.length
        ? `Founder call after ${round} fix rounds: the fold's decider (${d.seat}) agreed these findings are real and the founder chose to land anyway: ${list(agreed)}${disagreed.length ? ` Ruled outside the bar: ${list(disagreed)}` : ''}`
        : `The fold's decider (${d.seat}) ruled every open finding outside the piece's review bar: ${list(disagreed)}`,
      by: 'opus-seat', head: decision.head, tree: decision.tree, items: disagreed.map(({ item, reason }) => ({ item, reason })),
    }
    const extra = { must_fix: mustFix, override }
    if (args.release_carry !== 'off' && agreed.length && agreed.every(r => releaseMechanics(r.item))) {
      if (!mechanicsReviewUsed && carry?.status === 'ok') {
        mechanicsReviewUsed = true
        const retried = await carryRound(round, author, lbl('carry-decider'), 'Decide')
        if (retried?.status === 'ok') {
          rounds[round].carry_decider = retried
          // Repeat the same numbered round without spending a fix round.
          repeatReview = true
          round--
          continue
        }
      }
      return ended('held', 'FAIL', { ...extra, next: `release mechanics only: ${agreed.map(r => r.item).join('; ')}` })
    }
    if (!gateWhy && !decision.needs_founder) {
      if (agreed.length) return ended('split', 'FAIL', { must_fix: mustFix, guidance: decision.guidance, queue_via: 'none',
        next: `Opus splits this piece into smaller slices (one scenario each) or drops it; do not queue. Decider guidance: ${decision.guidance}` })
      return ended('override', 'FAIL', { ...extra, founder: founderAsk(p, d.out, rulings, authors, why), queue_via: 'override', next: nextOverride(decision) })
    }
    return ended('founder_call', 'FAIL', { ...extra, founder: founderAsk(p, d.out, rulings, authors, why), queue_via: 'founder', next: nextFounder(decision, why) })
  }
}

const pieces = (args && args.pieces) || []
let retiredImplLogged = false
if (!pieces.length) return { error: 'no pieces in args' }
if (!['block', 'warn', 'off'].includes(MONEY_FLOOR_MODE)) throw new Error(`money_path_floor must be block, warn or off; got ${showEntry(MONEY_FLOOR_MODE)}`)
if (args.release_carry !== undefined && !['on', 'off'].includes(args.release_carry)) throw new Error(`release_carry must be on or off; got ${showEntry(args.release_carry)}`)
// Collect piece-local input problems before any seat starts; valid pieces still run in their original order.
const problemsOf = p => {
  const problems = []
  for (const field of ['title', 'worktree', 'check'])
    if (typeof p?.[field] !== 'string') problems.push(`${field} must be a string`)
  if (!Array.isArray(p?.files) || !p.files.length || !p.files.every(f => typeof f === 'string'))
    problems.push('files must be a non-empty array of strings')
  const nodes = [
    ...(Array.isArray(p?.acceptance_tests) ? p.acceptance_tests : []),
    ...(p?.scenario?.type === 'pytest' && Array.isArray(p.scenario.nodes) ? p.scenario.nodes : []),
  ]
  for (const node of nodes)
    if (typeof node === 'string' && node.endsWith('.py') && !node.includes('::'))
      problems.push(`pytest node ${node} must name a test with ::test`)
  return problems
}
const specProblems = pieces.map(problemsOf)
// Checked for every runnable piece before the first agent() call, so a bad piece never starts a seat.
pieces.forEach((p, i) => {
  if (specProblems[i].length) return
  // 2026-09-28 14:51Z (Alex: "offload to the pc as quick as possible... balance the load by default"): box first. An
  // agent-rails piece implements on a free job box and falls back to Studio when every box is full; hosts: 'spill' keeps
  // the old Studio-first order, hosts: 'studio' never uses a box.
  if (args.hosts === 'studio') p.host = null
  else if (!p.host && (args.hosts === 'auto' || args.hosts === 'spill' || railsWorktree(p.worktree)))
    p.host = args.hosts === 'spill' ? 'spill' : 'auto'
  if (p.host && !p.repo && !args.repo && railsWorktree(p.worktree)) p.repo = 'agent-rails'
  if (p.implementer === 'luna-implementer') {
    p.implementer = 'gpt-implementer'
    if (!retiredImplLogged) {
      log('luna-implementer is retired; using gpt-implementer')
      retiredImplLogged = true
    }
  }
  checkSources(p)
  if (!IMPLEMENTERS.includes(implOf(p))) throw new Error(`piece "${p.title}": implementer "${implOf(p)}" is not one of ${IMPLEMENTERS.join(', ')}`)
  if (p.scenario == null && !(typeof p.owner_boundary === 'string' && p.owner_boundary.trim()))
    throw new Error(`piece "${p.title}": no scenario. Give a scenario-run spec, or owner_boundary: "<why this piece has no user-side scenario>" (review cutover rule 2)`)
  if (p.scenario != null && (typeof p.scenario !== 'object' || Array.isArray(p.scenario) || typeof p.scenario.type !== 'string'))
    throw new Error(`piece "${p.title}": scenario must be a scenario-run spec object with a string type`)
})

const results = await pipeline(pieces.map((p, i) => ({ p, problems: specProblems[i] })), async ({ p, problems }) => {
  if (problems.length) return { title: p?.title, worktree: p?.worktree, status: 'bad_spec', latest_verdict: 'NONE', queue_allowed: false,
    escalated: false, fix_seats: [], ran_on: [], problems, next: `fix the args: ${problems.join('; ')}` }
  try {
    return await foldPiece(p)
  } catch (error) {
    const message = String(error && error.message || error)
    return { title: p.title, worktree: p.worktree, status: 'pipeline_error', latest_verdict: 'NONE', queue_allowed: false, escalated: false, fix_seats: [], ran_on: ranOn.get(p) || [],
      error: message, next: `The fold template threw (${message}). Fix the template in the factory repo and rerun this piece; do not queue it.` }
  }
})

const out = results.map((r, i) => r || { title: pieces[i].title, status: 'pipeline_error', latest_verdict: 'NONE', queue_allowed: false,
  escalated: false, fix_seats: [], ran_on: ranOn.get(pieces[i]) || [], next: 'The fold stage returned nothing. Rerun this piece; do not queue it.' })
log(out.map(r => `${r.status} (queue_allowed=${r.queue_allowed}): ${r.title}${r.escalated ? ' [escalated]' : ''}`).join(' | '))
// Loud by design: every piece the lane cannot queue as is gets its own line with its next step.
const summary = {}
for (const r of out) summary[r.status] = (summary[r.status] || 0) + 1
const attention = out.filter(r => r.status !== 'pass' || !r.queue_allowed).map(r => ({ title: r.title, status: r.status, next: r.next || '' }))
log(`fold ${args.lane || ''}: ${Object.entries(summary).map(([k, n]) => `${n} ${k}`).join(', ')}${attention.length ? `; ${attention.length} need the lane` : ''}`)
for (const a of attention) log(`${a.status.toUpperCase()}: ${a.title}. ${a.next}`)
return { lane: args.lane, summary, attention, pieces: out }
