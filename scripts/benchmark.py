"""Measure ingestion throughput on a synthetic shapefile.

python -m scripts.benchmark --features 50000
"""

import argparse
import random
import tempfile
import time
from pathlib import Path

import shapefile

from app.config import Settings
from app.db import Database
from app.services.ingestion import ingest_upload
from tests import factories as f


def build(n: int) -> bytes:
    rng = random.Random(42)  # noqa: S311  (deterministic test data, not crypto)
    geoms, recs = [], []
    for i in range(n):
        lon, lat = 77.0 + rng.random(), 12.5 + rng.random()
        size = rng.uniform(0.0005, 0.005)  # ~50-500 m parcels
        geoms.append(f.square(lon, lat, size))
        recs.append({"name": f"parcel-{i}"})
    return f.shapefile_zip(shape_type=shapefile.POLYGON, geometries=geoms, records=recs, epsg=4326)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=int, default=50_000)
    args = parser.parse_args()

    data = build(args.features)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(database_url=f"sqlite:///{Path(tmp) / 'bench.db'}")
        db = Database(settings.database_url)
        db.create_all()
        with db.session_factory() as session:
            start = time.perf_counter()
            record = ingest_upload(session, settings, "bench.zip", data)
            elapsed = time.perf_counter() - start
        db.engine.dispose()

    print(f"{record.feature_count} features, {len(data) / 1e6:.1f} MB zip")
    print(f"total {elapsed:.2f} s  ->  {record.feature_count / elapsed:,.0f} features/s")


if __name__ == "__main__":
    main()
