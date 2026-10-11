FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Install dependencies first so this layer is cached across code changes.
COPY pyproject.toml README.md ./
RUN mkdir app && touch app/__init__.py && pip install . && rm -rf app

COPY app ./app
RUN pip install --no-deps . \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /srv/data && chown appuser /srv/data

USER appuser
EXPOSE 8000
VOLUME ["/srv/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
