# ---- Stage 1: build the web UI -------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: API + built UI ----------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# rasterio's wheel bundles GDAL but links the system Expat XML parser, which the
# slim image omits.
RUN apt-get update     && apt-get install -y --no-install-recommends libexpat1     && rm -rf /var/lib/apt/lists/*

# Install dependencies first so this layer is cached across code changes.
COPY pyproject.toml README.md ./
RUN mkdir app && touch app/__init__.py && pip install . && rm -rf app

COPY app ./app
COPY samples ./samples
COPY --from=ui /ui/dist ./frontend/dist
RUN pip install --no-deps . \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /srv/data && chown appuser /srv/data

USER appuser
EXPOSE 8000
VOLUME ["/srv/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# PORT lets platforms such as Render/Railway/Fly inject their port.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
