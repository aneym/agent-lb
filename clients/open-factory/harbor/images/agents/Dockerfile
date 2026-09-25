# of-work/agents: Ubuntu 24.04 with the Claude Code and Codex CLIs pre-installed,
# so Harbor's installers find them on PATH and skip the 3-6 minute install.
# Agent binaries live under /opt/of-agents/bin (symlinked into /usr/local/bin).
FROM ubuntu:24.04

ARG NODE_VERSION=22.23.3
ARG CLAUDE_CODE_VERSION=2.1.282
ARG CODEX_VERSION=0.157.0
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    DISABLE_AUTOUPDATER=1

# System deps Harbor's claude-code/codex installers would otherwise apt-install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq procps ripgrep xz-utils zstd \
 && apt-get clean && find /var/lib/apt/lists -mindepth 1 -delete

# Node 22 (codex's npm launcher needs it). Pinned tarball, checksum-verified.
RUN set -eu; \
    case "$TARGETARCH" in arm64) node_arch=arm64 ;; amd64) node_arch=x64 ;; *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; esac; \
    base="https://nodejs.org/dist/v${NODE_VERSION}"; tar="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    curl -fsSLO "$base/$tar"; curl -fsSL "$base/SHASUMS256.txt" | grep " $tar\$" | sha256sum -c -; \
    tar -xJf "$tar" -C /usr/local --strip-components=1 --exclude CHANGELOG.md --exclude README.md --exclude LICENSE; \
    rm "$tar"; node --version

# Claude Code native binary: the artifact bootstrap.sh installs, checksum-verified
# against the release manifest.
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

# Codex CLI via npm (what Harbor's installer does), pinned.
RUN npm install -g "@openai/codex@${CODEX_VERSION}" && npm cache clean --force && codex --version

# Non-root user for tasks that want one. Harbor runs as the image user (root here).
RUN useradd -m -s /bin/bash agent

RUN printf 'claude-code %s\ncodex %s\nnode %s\n' "$CLAUDE_CODE_VERSION" "$CODEX_VERSION" "$NODE_VERSION" > /opt/of-agents/VERSIONS

WORKDIR /app
