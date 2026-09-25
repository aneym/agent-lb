# syntax=docker/dockerfile:1
# of-work/<repo>-base: of-work/agents + uv + Python + one repo at a base commit
# with its test dependencies synced (by the repo's of-sync). Per-task images are:
#   FROM of-work/<repo>-base
#   RUN of-checkout <sha>
# Build through ../../build-images.sh, which puts a scratch bare repo (src.git,
# history up to BASE_SHA only) and the repo's of-sync in the build context.
ARG AGENTS_IMAGE=of-work/agents:latest
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19
FROM ${UV_IMAGE} AS uv
FROM ${AGENTS_IMAGE}

ARG PYTHON_VERSION=3.14.5
ARG BASE_SHA
ARG REPO_PYTHONPATH=

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_PYTHON=${PYTHON_VERSION} \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    PYTHONPATH=${REPO_PYTHONPATH}
RUN uv python install "${PYTHON_VERSION}"

RUN git config --system user.name "of-agent" \
 && git config --system user.email "of-agent@localhost" \
 && git config --system init.defaultBranch main \
 && git config --system --add safe.directory '*'

COPY of-checkout of-sync /usr/local/bin/

# src.git is bind-mounted, never stored in a layer.
RUN --mount=type=bind,source=src.git,target=/tmp/src.git \
    git init -q /app \
 && git -C /app fetch -q --no-tags --update-shallow /tmp/src.git "+refs/of/base:refs/of/base" \
 && git -C /app checkout -q -B main refs/of/base \
 && git -C /app update-ref -d refs/of/base \
 && rm -f /app/.git/FETCH_HEAD \
 && test "$(git -C /app rev-parse HEAD)" = "${BASE_SHA}"

WORKDIR /app
RUN of-sync /app
