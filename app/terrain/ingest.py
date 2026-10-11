"""DEM ingestion: validate an uploaded GeoTIFF and convert it to a Cloud-Optimised GeoTIFF.

COG (internal tiling plus overviews) is what makes the rest cheap. Analyses read
only the blocks under a polygon, and map tiles at low zoom read the small
overviews instead of the full-resolution grid. It is also how drone platforms
store elevation models: Aereo's public tile service reads COGs from S3.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
import rasterio.shutil
from pyproj import CRS
from rasterio.errors import RasterioError
from rasterio.warp import transform_bounds

from app.errors import InvalidGeoFile
from app.geo.crs import describe_crs
from app.terrain.grid import cell_metrics, is_north_up

STATS_TARGET_CELLS = 4_000_000


@dataclass
class DemInfo:
    crs: str | None
    crs_wkt: str
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    res: tuple[float, float]
    cell_size_m: tuple[float, float]
    bbox: list[float]
    z_min: float
    z_max: float
    z_mean: float
    valid_pct: float
    warnings: list[str] = field(default_factory=list)


def validate_and_convert(src_path: Path, dst_path: Path, max_pixels: int) -> DemInfo:
    warnings: list[str] = []
    try:
        src = rasterio.open(src_path)
    except RasterioError as exc:
        raise InvalidGeoFile("File is not a readable GeoTIFF.") from exc

    with src:
        if src.driver not in ("GTiff", "COG"):
            raise InvalidGeoFile(f"Expected a GeoTIFF, got a {src.driver} raster.")
        if src.crs is None:
            raise InvalidGeoFile("The GeoTIFF has no CRS, so it can't be placed or measured.")
        if not is_north_up(src.transform):
            raise InvalidGeoFile("Rotated or sheared rasters are not supported; please export north-up.")
        if src.width * src.height > max_pixels:
            raise InvalidGeoFile(f"DEM has {src.width * src.height:,} cells; the limit is {max_pixels:,}.")
        dtype = src.dtypes[0]
        if src.count >= 3 and dtype == "uint8":
            raise InvalidGeoFile(
                "This looks like imagery (3+ byte bands), not an elevation model. "
                "Upload a single-band DEM/DSM."
            )
        if src.count > 1:
            warnings.append(f"Raster has {src.count} bands; band 1 is used as elevation.")
        if src.nodata is None:
            warnings.append("No nodata value is declared; every cell is treated as a valid elevation.")

        options = {
            "COMPRESS": "DEFLATE",
            "PREDICTOR": "YES",  # floating-point predictor for float DEMs: ~2x smaller
            "BLOCKSIZE": "512",
            "OVERVIEW_RESAMPLING": "AVERAGE",
            "BIGTIFF": "IF_SAFER",
            "NUM_THREADS": "ALL_CPUS",
        }
        try:
            rasterio.shutil.copy(src, dst_path, driver="COG", **options)
        except RasterioError as exc:
            raise InvalidGeoFile(f"Could not convert the DEM to a Cloud-Optimised GeoTIFF: {exc}") from exc

    with rasterio.open(dst_path) as ds:
        crs = CRS.from_user_input(ds.crs)
        # Stats from a decimated read (overviews), so big DEMs don't load fully into memory.
        factor = max(1, math.ceil(math.sqrt(ds.width * ds.height / STATS_TARGET_CELLS)))
        sample = ds.read(1, out_shape=(max(1, ds.height // factor), max(1, ds.width // factor)), masked=True)
        z = np.ma.filled(sample.astype(np.float64), np.nan)
        finite = np.isfinite(z)
        if not finite.any():
            raise InvalidGeoFile("The DEM contains no valid elevation values.")
        valid_pct = float(finite.mean() * 100)
        zv = z[finite]
        metrics = cell_metrics(crs, ds.transform, ds.height, ds.width)
        mid = len(metrics.dx_m) // 2
        bbox = list(transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21))
        if factor > 1:
            warnings.append("Elevation statistics are estimated from overviews.")
        return DemInfo(
            crs=describe_crs(crs),
            crs_wkt=crs.to_wkt(),
            width=ds.width,
            height=ds.height,
            band_count=ds.count,
            dtype=ds.dtypes[0],
            nodata=None if ds.nodata is None or math.isnan(ds.nodata) else float(ds.nodata),
            res=(ds.transform.a, -ds.transform.e),
            cell_size_m=(round(float(metrics.dx_m[mid]), 4), round(float(metrics.dy_m[mid]), 4)),
            bbox=[round(v, 7) for v in bbox],
            z_min=round(float(zv.min()), 3),
            z_max=round(float(zv.max()), 3),
            z_mean=round(float(zv.mean()), 3),
            valid_pct=round(valid_pct, 2),
            warnings=warnings,
        )
