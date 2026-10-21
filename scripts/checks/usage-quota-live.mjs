import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const url = process.env.LB_USAGE_URL || "https://studio.tailf266ac.ts.net:2455/usage";
const out = process.env.LB_QUOTA_EVIDENCE || mkdtempSync(path.join(tmpdir(), "lb-quota-proof-"));
mkdirSync(out, { recursive: true });
const steps = path.join(out, "quota-steps.json");
writeFileSync(steps, JSON.stringify([
  { wait_fn: "() => [...document.querySelectorAll('[data-pool]')].some(e => /% remaining|unknown/.test(e.textContent))" },
  { eval: `async () => {
    const block = document.querySelector('[data-testid="subscription-quota"]');
    if (!block || !/% remaining|unknown/.test(block.textContent) || !/reset/.test(block.textContent)) throw Error('Quota percent/reset missing');
    const response = await fetch('/api/pools');
    if (!response.ok) throw Error('Pools API unavailable');
    const { pools } = await response.json();
    for (const pool of pools) {
      const row = [...block.querySelectorAll('[data-pool]')].find(e => e.dataset.pool === pool.id);
      if (!row) throw Error('Missing pool ' + pool.id);
      for (const [label, key] of [['5h', 'fiveHourRemainingPercent'], ['Weekly', 'weeklyRemainingPercent']]) {
        const expected = pool[key] == null ? 'unknown' : pool[key].toFixed(1) + '% remaining';
        if (!row.textContent.includes(label + ': ' + expected)) throw Error('Wrong quota ' + pool.id + ' ' + label);
      }
      if (!row.textContent.includes('reset')) throw Error('Reset missing ' + pool.id);
    }
    const secondary = [...document.querySelectorAll('h2')].find(e => e.textContent.includes('API list-price equivalent (not billed)'));
    if (!secondary || !(block.compareDocumentPosition(secondary) & Node.DOCUMENT_POSITION_FOLLOWING)) throw Error('API equivalent is not secondary');
    if (document.body.textContent.includes('Share of cost')) throw Error('Misleading cost label');
    return { pools: pools.length, quota: 'percent + reset', dollars: 'not billed', headline: 'subscription quota' };
  }` },
]));
const shot = spawnSync("page-shot", [url, "--isolated", "--out", out, "--widths", "1440", "--themes", "light", "--timeout", "15", "--viewport-only", "--steps", steps], { encoding: "utf8", timeout: 60_000, killSignal: "SIGKILL" });
process.stdout.write(shot.stdout || "");
process.stderr.write(shot.stderr || "");
assert.equal(shot.status, 0, "Live usage page proof failed");
const result = JSON.parse(shot.stdout);
assert.equal(result.ok, true);
assert.equal(result.shots.length, 1);
assert(result.shots.every(s => !s.overflow_x), "Horizontal overflow");
console.log("PASS live subscription quota: pools, percent/unknown, reset, secondary not-billed dollars; desktop light");
