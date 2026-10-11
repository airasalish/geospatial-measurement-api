"""Prometheus metrics, exposed at ``/metrics``.

Labels stay low-cardinality: route *templates* (``/api/files/{file_id}/``), never
raw paths, and fixed enums for kinds and statuses.
"""

import time
from collections.abc import Awaitable, Callable

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter("traverse_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "traverse_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
UPLOADS = Counter("traverse_uploads_processed_total", "Uploads processed", ["kind", "status"])
PROCESSING = Histogram(
    "traverse_processing_seconds",
    "Time to process an upload (parse/measure or validate/convert)",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
FEATURES = Counter("traverse_features_measured_total", "Vector features measured")
ANALYSIS = Histogram(
    "traverse_analysis_seconds",
    "Terrain analysis latency",
    ["analysis"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)


def observe_processing(kind: str, status: str, seconds: float, features: int = 0) -> None:
    UPLOADS.labels(kind=kind, status=str(status)).inc()
    if seconds > 0:
        PROCESSING.labels(kind=kind).observe(seconds)
    if features:
        FEATURES.inc(features)


async def http_metrics(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    template = getattr(route, "path", None) or ("static" if request.method == "GET" else "unmatched")
    HTTP_REQUESTS.labels(request.method, template, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method, template).observe(time.perf_counter() - start)
    return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
