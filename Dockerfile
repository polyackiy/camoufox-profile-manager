# One image, one process: FastAPI serves the REST API and the built web UI on a
# single port, exactly as `camoufox-pm` does outside a container. The UI is built
# in the first stage and copied in, so the runtime image carries no Node.
#
# Note: launching real browsers inside a container needs a virtual display
# (Camoufox headless="virtual" / Xvfb on Linux). This image runs the API, the
# scheduler and profile management; see docs/accessibility-roadmap.md.

FROM node:20-slim AS webui
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV NEXT_EXPORT=1
RUN npm run build

FROM python:3.12-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen

# The static export, where the package looks for it when no CPM_WEBUI_DIR is set.
COPY --from=webui /web/out ./src/camoufox_pm/webui

ENV CPM_HOST=0.0.0.0 \
    CPM_PORT=8000 \
    CPM_DB_PATH=/data/profiles.db

VOLUME ["/data"]
EXPOSE 8000

# The console script, so the container runs the same entry point as a local
# install rather than a second, drifting invocation of uvicorn.
CMD ["uv", "run", "camoufox-pm", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
