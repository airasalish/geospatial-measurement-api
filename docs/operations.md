# Operations

[← README](../README.md)

The same image runs as a single container (the demo) or as a production-shaped stack.

```text
                  ┌──────────────────────────────────────┐
  browser ──────► │ api  (FastAPI + built UI, uvicorn)   │── /metrics ◄── prometheus
                  │  • uploads → 202, raw file → S3      │
                  │  • analyses, tiles: read COGs in     │
                  │    place via GDAL /vsis3/ (ranges)   │
                  └───────┬───────────────┬──────────────┘
                          │ enqueue       │ SQL (PostGIS: GiST, ST_AsMVT)
                          ▼               ▼
                       redis ◄──── worker (Celery, same code as sync mode)
                                          │ parse, measure, COG-convert
                                          ▼
                                 S3-compatible storage (SeaweedFS locally, S3 in prod)
```

| Concern | How |
|---|---|
| **Schema** | Alembic migrations, applied at API startup. A test runs autogenerate against the models and fails on any drift. PostGIS-only DDL (extension, `geom` column, GiST index) is dialect-guarded |
| **Processing** | `GEO_TASK_MODE=celery`: the API stores the upload, creates the record (`PROCESSING`) and returns **202** with `Location`. The worker runs the *same* ingestion code as sync mode. Late acks + `prefetch=1`: a crashed worker's task is retried, not lost, and long DEM jobs don't queue behind each other |
| **Storage** | Keys, not paths. COGs on S3 are opened as `/vsis3/bucket/key`; endpoint settings go to GDAL, credentials via rasterio's `AWSSession`. Downloads are SigV4 presigned redirects |
| **Auth** | Off by default. With `GEO_AUTH_REQUIRED=true`: scrypt password hashes, HS256 JWTs, every file, DEM, analysis and tile scoped to its owner (others' ids return 404), optional projects. Refuses to start with a default or short secret |
| **Health** | `/health` (liveness) and `/health/ready` (database, storage, queue), returning **503** with per-check status when degraded |
| **Metrics** | Prometheus: request rate and latency by *route template* (bounded cardinality), uploads by kind and status, processing and terrain-analysis latency histograms |
| **Logs** | `GEO_LOG_FORMAT=json`: one JSON object per line |

## docker-compose

**docker-compose:** `docker compose up --build` starts the API (UI and docs on :8000),
the worker, PostGIS, Redis, S3 (SeaweedFS on :8333) and Prometheus (:9090). Startup is
health-gated: the API waits for its dependencies, and the worker waits for the API so
migrations run first. The worker's healthcheck pings it over the broker, because the
image's default probe is HTTP and a worker serves none.

## CI

**CI** (GitHub Actions, every push):

| Job | What it proves |
|---|---|
| `backend` (3.12, 3.13) | ruff, `mypy --strict`, 193 tests on SQLite, coverage ≥ 90% |
| `postgis` | The same suite against `postgis/postgis`: migrations, GiST index, `ST_AsMVT` |
| `frontend` | strict TypeScript, unit tests, production build |
| `compose` | Boots `docker-compose.yml`, checks readiness, pushes uploads through Redis, the containerised worker and S3, and checks Prometheus scrapes the API |
| `docker` | Builds the single-container image and smoke-tests the UI and an upload |

## Configuration

All settings are environment variables with a `GEO_` prefix. They can also be set
in a `.env` file (see [`.env.example`](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/geo.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `GEO_MAX_UPLOAD_BYTES` | 50 MB | Upload size limit (enforced while streaming) |
| `GEO_MAX_UNCOMPRESSED_BYTES` | 500 MB | Zip-bomb guard: total uncompressed size |
| `GEO_MAX_COMPRESSION_RATIO` | 200 | Zip-bomb guard: compression ratio |
| `GEO_MAX_ARCHIVE_MEMBERS` | 1000 | Max files inside a zip |
| `GEO_MAX_FEATURES` | 250,000 | Max features per file |
| `GEO_DEFAULT_PAGE_SIZE` / `GEO_MAX_PAGE_SIZE` | 100 / 1000 | Pagination |
| `GEO_MAX_GEOJSON_FEATURES` | 100,000 | Cap for the map endpoint |
| `GEO_CORS_ORIGINS` | *(none)* | Comma-separated origins, only needed if the UI is hosted elsewhere |
| `GEO_MAX_RASTER_UPLOAD_BYTES` / `GEO_MAX_RASTER_PIXELS` | 500 MB / 250 M | DEM upload limits |
| `GEO_STORAGE_BACKEND` | `local` | `local` (`GEO_STORAGE_DIR`) or `s3` (`GEO_S3_BUCKET`, `GEO_S3_ENDPOINT_URL`, keys) |
| `GEO_TASK_MODE` | `sync` | `sync` (in the request) or `celery` (202 + worker; `GEO_REDIS_URL`) |
| `GEO_AUTH_REQUIRED` | `false` | Require sign-in; needs `GEO_JWT_SECRET` (32+ bytes) |
| `GEO_LOG_FORMAT` | `text` | `json` for log shippers |
