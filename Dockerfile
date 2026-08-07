# Backend image: FastAPI + Camoufox Profile Manager.
#
# Note: launching real browsers inside a container needs a virtual display
# (Camoufox headless="virtual" / Xvfb on Linux). This image runs the API and
# profile management; see docs/accessibility-roadmap.md for the browser caveat.
FROM python:3.12-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen

ENV CPM_HOST=0.0.0.0 \
    CPM_PORT=8000 \
    CPM_DB_PATH=/data/profiles.db \
    CPM_CORS_ORIGINS=http://localhost:3000

VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "camoufox_pm.main:app", "--host", "0.0.0.0", "--port", "8000"]
