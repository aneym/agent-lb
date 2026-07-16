#!/usr/bin/env node
// agent-lb-front — always-up TCP front for the agent-lb service.
//
// Holds the public localhost port (default 2455, the one tailscale serve and
// local clients target) and pipes raw TCP to the app on an internal port
// (default 2457). While the app is down or booting — deploys take 60-80s under
// host load — new connections are HELD and the upstream connect is retried
// instead of refused, so tailscale serve never turns a restart into
// "502 Bad Gateway" for every in-flight chat. This process is deliberately
// dumb (no HTTP parsing, no deps) so it never needs a restart of its own.
//
// Env: AGENT_LB_FRONT_LISTEN_PORT (2455), AGENT_LB_FRONT_UPSTREAM_PORT (2457),
//      AGENT_LB_FRONT_HOLD_MS (180000), AGENT_LB_FRONT_RETRY_INTERVAL_MS (500),
//      AGENT_LB_FRONT_HOST (127.0.0.1)

import net from "node:net";

const HOST = process.env.AGENT_LB_FRONT_HOST || "127.0.0.1";
const LISTEN_PORT = Number(process.env.AGENT_LB_FRONT_LISTEN_PORT || "2455");
const UPSTREAM_PORT = Number(process.env.AGENT_LB_FRONT_UPSTREAM_PORT || "2457");
const HOLD_MS = Number(process.env.AGENT_LB_FRONT_HOLD_MS || "180000");
const RETRY_INTERVAL_MS = Number(
  process.env.AGENT_LB_FRONT_RETRY_INTERVAL_MS || "500",
);

let lastHoldLogMs = 0;

// Logging is best-effort: a full disk once crashed the whole front with an
// unhandled ENOSPC 'error' event from launchd's stdout stream (2026-07-14),
// taking port 2455 down with it. Swallow stream errors — proxying must
// outlive the log file.
process.stdout.on("error", () => {});

function log(message) {
  try {
    process.stdout.write(`${new Date().toISOString()} ${message}\n`);
  } catch {
    // ENOSPC/EPIPE on the log stream must never kill the proxy.
  }
}

function connectUpstream(deadlineMs, onConnect, onGiveUp) {
  const socket = net.connect({ host: HOST, port: UPSTREAM_PORT });
  socket.once("connect", () => onConnect(socket));
  socket.once("error", () => {
    socket.destroy();
    if (Date.now() >= deadlineMs) {
      onGiveUp();
      return;
    }
    const now = Date.now();
    if (now - lastHoldLogMs > 5000) {
      lastHoldLogMs = now;
      log(`upstream :${UPSTREAM_PORT} down — holding connections`);
    }
    setTimeout(
      () => connectUpstream(deadlineMs, onConnect, onGiveUp),
      RETRY_INTERVAL_MS,
    );
  });
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
    (upstream) => {
      upstream.on("error", () => {});
      if (clientClosed) {
        upstream.destroy();
        return;
      }
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

server.on("error", (error) => {
  log(`fatal server error: ${error.message}`);
  process.exitCode = 1;
  server.close();
});

server.listen(LISTEN_PORT, HOST, () => {
  log(
    `agent-lb-front listening on ${HOST}:${LISTEN_PORT} -> ${HOST}:${UPSTREAM_PORT} (hold ${HOLD_MS}ms)`,
  );
});
