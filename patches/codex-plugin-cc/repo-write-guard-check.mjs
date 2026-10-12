import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
const installed=path.join(os.homedir(),'.agent-lb/plugins/codex-plugin-cc');
const scratch=process.env.GUARD_CHECK_TMPDIR ?? os.tmpdir();
const fixture=fs.mkdtempSync(path.join(scratch,'guard-check-'));
fs.cpSync(path.join(installed,'plugins/codex'),path.join(fixture,'plugins/codex'),{recursive:true});
const patch=path.resolve('patches/codex-plugin-cc/repo-write-guard.patch');
let applied=spawnSync('git',['apply','--reverse','--check',patch],{cwd:fixture,encoding:'utf8'});
if(applied.status!==0){applied=spawnSync('git',['apply',patch],{cwd:fixture,encoding:'utf8'});assert.equal(applied.status,0,applied.stderr);}
const wrapper=process.env.GUARD_INSTALLED==='1'?path.join(installed,'plugins/codex/scripts/codex-companion.mjs'):path.join(fixture,'plugins/codex/scripts/codex-companion.mjs');
const env={...process.env,CODEX_COMPANION_DEPTH:'0'};
const brief=path.join(fixture,'brief.txt');fs.writeFileSync(brief,'Edit /Volumes/StudioExt/repos/agent-rails-wt/x');
function run(label,args,refuse=false,cwd=fixture,extra={}){
const r=spawnSync(process.execPath,[wrapper,'task','--dry-run',...args],{cwd,env:{...env,...extra},encoding:'utf8'});
assert.equal(r.status,refuse?1:0,`${label}: ${r.stdout}${r.stderr}`);
if(refuse){assert.match(r.stderr,/repo edits go through seat-run gpt-implementer \/ seat-exec, never codex-companion --write/);assert.equal(r.stderr.trim().split('\n').length,1);}
else assert.match(r.stdout,/dry-run passed/);
console.log(`PASS ${label}`);
}
run('brief repo write refuses',['--write','--prompt-file',brief],true);
run('explicit override passes',['--write','--allow-repo-write','--prompt-file',brief]);
run('read only passes',['--prompt-file',brief]);
run('cwd repo write refuses',['--write','inspect'],true,process.cwd());
run('cwd override passes',['--write','--allow-repo-write','inspect'],false,process.cwd());
run('lane exception passes',['--write','inspect'],false,process.cwd(),{LANE_DIR:process.cwd()});
run('background refuses before queue',['--write','--background','--prompt-file',brief],true);
run('boundary sibling passes',['--write','Edit /Volumes/StudioExt/repos-other/x']);
fs.rmSync(fixture,{recursive:true});
console.log('All 8 wrapper dry-run checks passed');
