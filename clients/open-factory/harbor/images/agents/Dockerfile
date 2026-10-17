FROM ubuntu:24.04

ARG NODE_VERSION=22.23.3
ARG CLAUDE_CODE_VERSION=2.1.282
ARG CODEX_VERSION=0.157.0
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    DISABLE_AUTOUPDATER=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq procps ripgrep xz-utils zstd \
 && apt-get clean && find /var/lib/apt/lists -mindepth 1 -delete

RUN set -eu; \
    case "$TARGETARCH" in arm64) node_arch=arm64 ;; amd64) node_arch=x64 ;; *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; esac; \
    base="https://nodejs.org/dist/v${NODE_VERSION}"; tar="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    curl -fsSLO "$base/$tar"; curl -fsSL "$base/SHASUMS256.txt" | grep " $tar\$" | sha256sum -c -; \
    tar -xJf "$tar" -C /usr/local --strip-components=1 --exclude CHANGELOG.md --exclude README.md --exclude LICENSE; \
    rm "$tar"; node --version

RUN set -eu; \
    case "$TARGETARCH" in arm64) platform=linux-arm64 ;; amd64) platform=linux-x64 ;; esac; \
    base="https://downloads.claude.ai/claude-code-releases/${CLAUDE_CODE_VERSION}"; \
    sum="$(curl -fsSL "$base/manifest.json" | jq -r ".platforms[\"$platform\"].checksum")"; \
    mkdir -p /opt/of-agents/bin; \
    curl -fsSL -o /opt/of-agents/bin/claude "$base/$platform/claude"; \
    echo "$sum  /opt/of-agents/bin/claude" | sha256sum -c -; \
    chmod 755 /opt/of-agents/bin/claude; \
    ln -sf /opt/of-agents/bin/claude /usr/local/bin/claude; \
    claude --version

RUN npm install -g "@openai/codex@${CODEX_VERSION}" && npm cache clean --force && codex --version

RUN useradd -m -s /bin/bash agent

RUN printf 'claude-code %s\ncodex %s\nnode %s\n' "$CLAUDE_CODE_VERSION" "$CODEX_VERSION" "$NODE_VERSION" > /opt/of-agents/VERSIONS

COPY pi-durable/package.json pi-durable/package-lock.json /opt/pi-durable/
RUN cd /opt/pi-durable && npm ci && npm cache clean --force
COPY pi-durable/pd-run.ts /opt/pi-durable/pd-run.ts
RUN mkdir -p /opt/pi-durable/bin /opt/pi-durable/lib \
 && cp /usr/local/bin/node /opt/pi-durable/bin/node-bin \
 && ldd /usr/local/bin/node | awk '/=> \// {print $3} /^\s*\// {print $1}' | xargs -I '{}' cp -L '{}' /opt/pi-durable/lib/ \
 && loader="$(ldd /usr/local/bin/node | awk '/ld-linux/ {for (i=1; i<=NF; i++) if ($i ~ /^\//) print $i}')" \
 && cp -L "$loader" /opt/pi-durable/lib/ \
 && printf '#!/bin/sh\nexec /opt/pi-durable/lib/ld-linux-*.so.* --library-path /opt/pi-durable/lib /opt/pi-durable/bin/node-bin "$@"\n' > /opt/pi-durable/bin/node \
 && chmod 755 /opt/pi-durable/bin/node \
 && /opt/pi-durable/bin/node --version
RUN node --experimental-strip-types /opt/pi-durable/pd-run.ts 2>&1 | grep "usage: pd-run.ts"

WORKDIR /app
