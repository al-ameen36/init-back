# Stage 1: grab the Node.js runtime (Debian/bookworm to match the Python
# base's glibc; Alpine would break our manylinux wheels like graph-sitter).
FROM node:20-bookworm-slim AS node_base

# Stage 2: the Python backend (uv-managed).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=600 \
    UV_HTTP_RETRIES=5

# graph-sitter depends on GitPython, which needs the `git` binary at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Bring Node + npm/npx from the node stage so we can run the specfy analyser.
COPY --from=node_base /usr/local/bin/node /usr/local/bin/node
COPY --from=node_base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Pre-install the specfy stack-analyser next to the app (prebuilt dist, no
# per-request download). The Node wrapper in tools/analyse.mjs imports it.
RUN mkdir -p /opt/analyser && \
    cd /opt/analyser && \
    npm init -y >/dev/null 2>&1 && \
    npm install @specfy/stack-analyser

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
COPY tools/analyse.mjs /opt/analyser/analyse.mjs

EXPOSE 8000

CMD ["uv", "run", "fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
