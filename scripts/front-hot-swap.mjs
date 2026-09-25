// Hot-swap a running agent-lb-front (old build) to the new build in-process.
// The inspector context has no dynamic import, so the new module's source is
// rewritten to take builtins from process.getBuiltinModule and evaluated as a
// script. Its server.listen is deferred: the new server is built first, then
// the old listener closes and the new one binds in the same turn, so the
// listen gap is sub-millisecond and existing piped connections are untouched.
//
// Use it to upgrade the live front: copy the new agent-lb-front.mjs into the
// runtime (the launchd job's file, so a later real restart runs the same
// code), then
//   node scripts/front-hot-swap.mjs [node-pid] [new-front-path]
// pid defaults to the process listening on LISTEN_PORT (2455); the path
// defaults to the runtime copy. Opens the Node inspector on 127.0.0.1:9229 for
// the swap and closes it again.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
const listenPort = Number(process.env.LISTEN_PORT || 2455);
const pid =
  process.argv[2] ||
  execFileSync("lsof", ["-tiTCP@127.0.0.1:" + listenPort, "-sTCP:LISTEN"], { encoding: "utf8" }).trim();
const newPath =
  process.argv[3] || os.homedir() + "/.agent-lb/runtime/agent-lb/scripts/agent-lb-front.mjs";
let src = fs.readFileSync(newPath, "utf8");
src = src.replace(/^#!.*\n/, "");
src = src.replace(/^import (\w+) from "(node:\w+)";$/gm, 'const $1 = process.getBuiltinModule("$2");');
if (/^import /m.test(src)) throw new Error("unhandled import in new front");
// Defer the bind: expose the listen call instead of running it.
const marker = "server.listen(LISTEN_PORT, HOST, () => {";
if (!src.includes(marker)) throw new Error("listen marker not found");
src = src.replace(marker, "return () => server.listen(LISTEN_PORT, HOST, () => {");
src = src.replace(/\}\);\s*$/, "}); ");
process.kill(Number(pid), "SIGUSR1");
let list;
for (let i = 0; i < 50; i++) {
  try { list = await (await fetch("http://127.0.0.1:9229/json/list")).json(); break; }
  catch { await new Promise(r => setTimeout(r, 100)); }
}
if (!list) throw new Error("inspector did not open");
const ws = new WebSocket(list[0].webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const call = (method, params) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const expr = `(() => {
  const servers = process._getActiveHandles().filter(h => h && h.constructor && h.constructor.name === "Server" && typeof h.address === "function" && h.address() && h.address().port === ${listenPort});
  if (servers.length !== 1) return "found " + servers.length + " listening servers; aborting";
  const listen = (function () { ${src} })();
  if (typeof listen !== "function") return "new front did not build; aborting";
  const t0 = process.hrtime.bigint();
  servers[0].close();
  listen();
  const t1 = process.hrtime.bigint();
  setTimeout(() => process.getBuiltinModule("node:inspector").close(), 300);
  return "swapped; close+listen took " + (Number(t1 - t0) / 1e6).toFixed(3) + "ms";
})()`;
console.log(`hot-swapping front pid ${pid} on :${listenPort} to ${newPath}`);
const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true });
console.log(JSON.stringify(res.result?.result?.value ?? res.result?.exceptionDetails ?? res));
ws.close();
