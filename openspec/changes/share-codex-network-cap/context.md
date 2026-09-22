# Context

The proxy is deliberately transport-only. It does not inspect TLS, choose
models, change effort, limit request count, or change Agent LB routing. Its
default 40 Mbps rate applies to the sum of uploads and downloads across every
proxy-aware process using the shared daemon. A 50 ms burst avoids penalizing
small control traffic while bounding queueing and preventing small writes from
bypassing accounting.

The daemon remains alive after launch so an idle but still-running Codex
process never retains proxy variables pointing at an expired listener. The
state file is mode 0600 under a mode 0700 directory and contains a random proxy
credential. Startup is serialized with `flock`. If the recorded PID is alive
but its authenticated health request fails, launch fails closed rather than
creating a second independent bucket.

Rollback is to stop selecting the launcher for new sessions. Setting
`CODEX_LB_TUNNEL_RATE_MBPS=0` disables shaping for a newly created daemon, but
does not reconfigure an already-running daemon.
