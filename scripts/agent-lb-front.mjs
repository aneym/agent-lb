#!/usr/bin/env node


























import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const HOST = process.env.AGENT_LB_FRONT_HOST || "127.0.0.1";
const LISTEN_PORT = Number(process.env.AGENT_LB_FRONT_LISTEN_PORT || "2455");
const UPSTREAM_PORTS = (
  process.env.AGENT_LB_FRONT_UPSTREAM_PORTS ||
  [process.env.AGENT_LB_FRONT_UPSTREAM_PORT || "2457", "2459"].join(",")
)
  .split(",")
  .map((p) => Number(p.trim()))
  .filter((p) => Number.isInteger(p) && p > 0);
const STATE_DIR = path.join(os.homedir(), ".agent-lb", "state");
const PREFERRED_FILE =
  process.env.AGENT_LB_FRONT_PREFERRED_FILE ||
  path.join(STATE_DIR, "front-preferred-port");
const STATE_FILE =
  process.env.AGENT_LB_FRONT_STATE_FILE || path.join(STATE_DIR, "front.json");
const HOLD_MS = Number(process.env.AGENT_LB_FRONT_HOLD_MS || "180000");
const RETRY_INTERVAL_MS = Number(
  process.env.AGENT_LB_FRONT_RETRY_INTERVAL_MS || "250",
);

let lastHoldLogMs = 0;





process.stdout.on("error", () => {});

function log(message) {
  try {
    process.stdout.write(`${new Date().toISOString()} ${message}\n`);
  } catch {

  }
}



let preferredCache = { mtimeMs: -1, port: UPSTREAM_PORTS[0] };
function preferredPort() {
  let stat;
  try {
    stat = fs.statSync(PREFERRED_FILE);
  } catch {
    if (preferredCache.mtimeMs !== 0) {
      preferredCache = { mtimeMs: 0, port: UPSTREAM_PORTS[0] };
    }
    return preferredCache.port;
  }
  if (stat.mtimeMs !== preferredCache.mtimeMs) {
    let port = UPSTREAM_PORTS[0];
    try {
      const value = Number(fs.readFileSync(PREFERRED_FILE, "utf8").trim());
      if (UPSTREAM_PORTS.includes(value)) port = value;
    } catch {

    }
    if (port !== preferredCache.port) log(`preferred upstream now :${port}`);
    preferredCache = { mtimeMs: stat.mtimeMs, port };
  }
  return preferredCache.port;
}

function upstreamOrder() {
  const first = preferredPort();
  return [first, ...UPSTREAM_PORTS.filter((p) => p !== first)];
}

const openByPort = new Map(UPSTREAM_PORTS.map((p) => [p, 0]));



function connectUpstream(deadlineMs, onConnect, onGiveUp) {
  const order = upstreamOrder();
  const errors = [];
  const attempt = (index) => {
    if (index >= order.length) {
      if (Date.now() >= deadlineMs) {
        onGiveUp();
        return;
      }
      const now = Date.now();
      if (now - lastHoldLogMs > 5000) {
        lastHoldLogMs = now;


        log(`no upstream up (${errors.join(", ")}) — holding connections`);
      }
      setTimeout(
        () => connectUpstream(deadlineMs, onConnect, onGiveUp),
        RETRY_INTERVAL_MS,
      );
      return;
    }
    const port = order[index];
    const socket = net.connect({ host: HOST, port });
    socket.once("connect", () => {
      socket.removeAllListeners("error");
      onConnect(socket, port);
    });
    socket.once("error", (error) => {
      socket.destroy();
      errors.push(`:${port} ${error?.code || "unknown"}`);
      attempt(index + 1);
    });
  };
  attempt(0);
}

const server = net.createServer({ noDelay: true }, (client) => {
  client.on("error", () => {});
  client.pause();
  let clientClosed = false;
  client.once("close", () => {
    clientClosed = true;
  });

  connectUpstream(
    Date.now() + HOLD_MS,
    (upstream, port) => {
      upstream.on("error", () => {});
      if (clientClosed) {
        upstream.destroy();
        return;
      }
      openByPort.set(port, (openByPort.get(port) || 0) + 1);
      upstream.once("close", () =>
        openByPort.set(port, (openByPort.get(port) || 1) - 1),
      );
      upstream.setNoDelay(true);
      client.pipe(upstream);
      upstream.pipe(client);
      client.once("close", () => upstream.destroy());
      upstream.once("close", () => client.destroy());
      client.resume();
    },
    () => {
      log(`gave up after ${HOLD_MS}ms — closing held connection`);
      client.destroy();
    },
  );
});



function writeState() {
  const state = {
    pid: process.pid,
    listen: LISTEN_PORT,
    upstreams: UPSTREAM_PORTS,
    preferred_file: PREFERRED_FILE,
    preferred: preferredCache.port,
    open_by_port: Object.fromEntries(openByPort),
    updated_at: new Date().toISOString(),
  };
  try {
    fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
    fs.writeFileSync(`${STATE_FILE}.tmp`, JSON.stringify(state));
    fs.renameSync(`${STATE_FILE}.tmp`, STATE_FILE);
  } catch {

  }
}

server.on("error", (error) => {
  log(`fatal server error: ${error.message}`);
  process.exitCode = 1;
  server.close();
});

server.listen(LISTEN_PORT, HOST, () => {
  preferredPort();
  writeState();
  setInterval(() => {
    preferredPort();
    writeState();
  }, 1000).unref();
  log(
    `agent-lb-front listening on ${HOST}:${LISTEN_PORT} -> ${HOST}:[${UPSTREAM_PORTS.join(",")}] preferred :${preferredCache.port} (hold ${HOLD_MS}ms)`,
  );
});
