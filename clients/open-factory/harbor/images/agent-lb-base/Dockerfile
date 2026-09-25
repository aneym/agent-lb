# syntax=docker/dockerfile:1
# of-work/agent-lb-base: of-work/agents + uv + Python + agent-lb at a base commit
# with its dev deps synced. Per-task images are:
#   FROM of-work/agent-lb-base
#   RUN of-checkout <sha>
# Build through ../../build-images.sh, which supplies repo.bundle in the context.
ARG AGENTS_IMAGE=of-work/agents:latest
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19
FROM ${UV_IMAGE} AS uv
FROM ${AGENTS_IMAGE}

ARG PYTHON_VERSION=3.14.5
ARG BASE_SHA

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_PYTHON=${PYTHON_VERSION} \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy
RUN uv python install "${PYTHON_VERSION}"

RUN git config --system user.name "of-agent" \
 && git config --system user.email "of-agent@localhost" \
 && git config --system init.defaultBranch main \
 && git config --system --add safe.directory '*'

# The bundle is bind-mounted, never stored in a layer. It holds only history
# reachable from BASE_SHA, so local branches and stashes never enter the image.
RUN --mount=type=bind,source=repo.bundle,target=/tmp/repo.bundle \
    git init -q /app \
 && git -C /app fetch -q /tmp/repo.bundle "+refs/of/base:refs/of/base" \
 && git -C /app checkout -q -B main refs/of/base \
 && git -C /app update-ref -d refs/of/base \
 && rm -f /app/.git/FETCH_HEAD \
 && test "$(git -C /app rev-parse HEAD)" = "${BASE_SHA}"

WORKDIR /app
RUN uv sync --frozen --dev && uv run python --version

COPY of-checkout /usr/local/bin/of-checkout
RUN chmod 755 /usr/local/bin/of-checkout
