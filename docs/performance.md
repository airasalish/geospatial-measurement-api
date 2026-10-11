# Performance

[← README](../README.md)

Run `python -m scripts.benchmark --features 50000` to generate a synthetic 50k-polygon
Shapefile and time the full ingestion: read, measure, encode and insert.

| Version | Throughput (50k polygons, laptop) |
|---|---|
| Naive: per-feature transforms, ORM inserts | ~1,400 features/s |
| Vectorised batch pipeline (current) | **~5,000–7,800 features/s** |

What made the difference, as found by profiling with `cProfile`:

1. **Vectorised reprojection.** One pyproj call per batch instead of three per
   feature, and one call per *projection-centre group* instead of per feature.
2. **Recomputed cache keys.** `crs.to_wkt()` was running for every feature
   (2.2 s per 20k features). It now runs once per batch.
3. **Core `executemany` instead of ORM bulk insert.** Skips per-row ORM bookkeeping.
4. **Vectorised `shapely.to_geojson`**, stored as text: no dict round trip through `json`.
5. **Conditional work.** `make_valid` and densification run only where they are needed,
   based on vectorised `is_valid` and vectorised geodesic segment lengths.

The remaining time is mostly the pure-Python Shapefile reader. See [Future scope](../README.md#future-scope).
