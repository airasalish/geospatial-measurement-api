"""DEM analytics: volumes, surface area, slope, profiles and point elevations.

Conventions (same as Pix4D and DroneDeploy, so numbers are comparable):

* **cut**: material *above* the base surface (what you would excavate or haul
  away; for a stockpile, its volume).
* **fill**: empty space *below* the base surface (what you would need to fill;
  for a pit, its volume).
* **net** = cut - fill.

Base surfaces, all estimated from the DEM along the polygon boundary unless stated:

| method         | base                                                          |
|----------------|---------------------------------------------------------------|
| `lowest`       | flat, at the lowest boundary elevation                        |
| `highest`      | flat, at the highest boundary elevation                       |
| `mean`         | flat, at the mean boundary elevation                          |
| `plane`        | least-squares plane through the boundary (sloped ground)      |
| `triangulated` | TIN through the boundary samples (default; irregular ground)  |
| `custom`       | flat, at a user-given elevation                               |
| `surface`      | another DEM: earlier survey or design surface (DEM of difference) |

Cells count when their centre lies inside the polygon. Cell areas come from
:mod:`app.terrain.grid`, so volumes are in true cubic metres in any CRS.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import rasterio
import shapely
from pyproj import CRS, Geod, Transformer
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window, from_bounds
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.ndimage import map_coordinates
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from app.errors import GeoAPIError
from app.terrain.grid import cell_metrics

GEOD = Geod(ellps="WGS84")
WGS84 = CRS.from_epsg(4326)
FloatArray = npt.NDArray[np.float64]
BaseMethod = Literal["lowest", "highest", "mean", "plane", "triangulated", "custom", "surface"]

BOUNDARY_METHODS = ("lowest", "highest", "mean", "plane", "triangulated")
MAX_ANALYSIS_CELLS = 40_000_000  # ~320 MB of float64 working arrays; beyond that, ask for a smaller area
MAX_PROFILE_SAMPLES = 5_000


class TerrainError(GeoAPIError):
    status_code = 422
    code = "terrain_error"


# --------------------------------------------------------------------- helpers


def _to_crs(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    if src == dst:
        return geom
    t = Transformer.from_crs(src, dst, always_xy=True)
    out: BaseGeometry = shapely.transform(geom, lambda c: np.column_stack(t.transform(c[:, 0], c[:, 1])))
    return out


@dataclass
class _Window:
    """A DEM window read into memory, with NaN for nodata."""

    z: FloatArray
    transform: rasterio.Affine
    window: Window


def _read_window(
    ds: rasterio.io.DatasetReader, bounds: tuple[float, float, float, float], pad: int = 2
) -> _Window:
    raw = from_bounds(*bounds, transform=ds.transform)
    col0 = max(0, math.floor(raw.col_off) - pad)
    row0 = max(0, math.floor(raw.row_off) - pad)
    col1 = min(ds.width, math.ceil(raw.col_off + raw.width) + pad)
    row1 = min(ds.height, math.ceil(raw.row_off + raw.height) + pad)
    if col1 <= col0 or row1 <= row0:
        raise TerrainError("Geometry does not overlap the DEM.")
    win = Window(col0, row0, col1 - col0, row1 - row0)
    if win.width * win.height > MAX_ANALYSIS_CELLS:
        raise TerrainError(
            f"Area covers {int(win.width * win.height):,} DEM cells; the limit is {MAX_ANALYSIS_CELLS:,}. "
            "Use a smaller polygon or a coarser DEM."
        )
    data = ds.read(1, window=win, masked=True).astype(np.float64)
    z = np.ma.filled(data, np.nan)
    z[~np.isfinite(z)] = np.nan
    return _Window(z=z, transform=ds.window_transform(win), window=win)


def _cell_centres(transform: rasterio.Affine, rows: int, cols: int) -> tuple[FloatArray, FloatArray]:
    xs = transform.c + transform.a * (np.arange(cols) + 0.5)
    ys = transform.f + transform.e * (np.arange(rows) + 0.5)
    return np.meshgrid(xs, ys)


def _sample_bilinear(z: FloatArray, transform: rasterio.Affine, x: FloatArray, y: FloatArray) -> FloatArray:
    """Bilinear DEM sample at map coordinates; NaN near nodata or outside."""
    col = (x - transform.c) / transform.a - 0.5
    row = (y - transform.f) / transform.e - 0.5
    filled = np.where(np.isnan(z), 0.0, z)
    values = map_coordinates(filled, [row, col], order=1, mode="nearest")
    # Any nodata among the 4 neighbours, or outside the window, poisons the sample.
    nan_mask = map_coordinates(np.isnan(z).astype(float), [row, col], order=1, mode="constant", cval=1.0)
    outside = (row < -0.5) | (col < -0.5) | (row > z.shape[0] - 0.5) | (col > z.shape[1] - 0.5)
    values[(nan_mask > 1e-9) | outside] = np.nan
    return np.asarray(values, dtype=np.float64)


def _boundary_samples(poly: BaseGeometry, win: _Window) -> tuple[FloatArray, FloatArray, FloatArray]:
    """DEM elevations along the polygon's outer boundary, about one sample per cell."""
    step = min(abs(win.transform.a), abs(win.transform.e))
    rings = [p.exterior for p in getattr(poly, "geoms", [poly])]
    xs, ys = [], []
    for ring in rings:
        dense = shapely.segmentize(ring, max_segment_length=step)
        coords = np.asarray(dense.coords)[:-1]
        xs.append(coords[:, 0])
        ys.append(coords[:, 1])
    x, y = np.concatenate(xs), np.concatenate(ys)
    z = _sample_bilinear(win.z, win.transform, x, y)
    ok = np.isfinite(z)
    if ok.sum() < 3:
        raise TerrainError("Not enough valid DEM values along the polygon boundary to build a base surface.")
    return x[ok], y[ok], z[ok]


def _slope_components(z: FloatArray, dx: FloatArray, dy: FloatArray) -> tuple[FloatArray, FloatArray]:
    """dz/dx, dz/dy in metres per metre (central differences; NaN-safe at gaps)."""
    gy, gx = np.gradient(z)
    # np.gradient works in cell units; rows run north->south so flip the sign of gy.
    return gx / dx, -gy / dy


# --------------------------------------------------------------------- volumes


@dataclass
class VolumeResult:
    base_method: str
    base_elevation: dict[str, float]
    cut_m3: float
    fill_m3: float
    net_m3: float
    area_2d_m2: float
    surface_area_3d_m2: float
    elevation: dict[str, float]
    max_height_above_base_m: float
    max_depth_below_base_m: float
    slope: dict[str, float]
    cells: dict[str, int]
    coverage_pct: float
    cell_size_m: tuple[float, float]
    cell_method: str
    plane: dict[str, float] | None = None
    warnings: list[str] = field(default_factory=list)


def _base_surface(
    method: BaseMethod,
    boundary: tuple[FloatArray, FloatArray, FloatArray] | None,
    win: _Window,
    xx: FloatArray,
    yy: FloatArray,
    inside: npt.NDArray[np.bool_],
    custom: float | None,
    other: rasterio.io.DatasetReader | None,
    ds_crs: CRS,
) -> tuple[FloatArray, dict[str, float] | None]:
    shape = win.z.shape
    if method == "custom":
        if custom is None:
            raise TerrainError("Base method 'custom' needs 'elevation'.")
        return np.full(shape, float(custom)), None
    if method == "surface":
        if other is None:
            raise TerrainError("Base method 'surface' needs 'raster_id' of another DEM.")
        base = np.full(shape, np.nan, dtype=np.float64)
        reproject(
            source=rasterio.band(other, 1),
            destination=base,
            dst_transform=win.transform,
            dst_crs=ds_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        return base, None

    assert boundary is not None
    bx, by, bz = boundary
    if method == "lowest":
        return np.full(shape, bz.min()), None
    if method == "highest":
        return np.full(shape, bz.max()), None
    if method == "mean":
        return np.full(shape, bz.mean()), None
    if method == "plane":
        # z = a*x + b*y + c, centred for numerical stability.
        x0, y0 = bx.mean(), by.mean()
        a_mat = np.column_stack([bx - x0, by - y0, np.ones_like(bx)])
        (a, b, c), *_ = np.linalg.lstsq(a_mat, bz, rcond=None)
        return a * (xx - x0) + b * (yy - y0) + c, {"dz_dx": float(a), "dz_dy": float(b), "z0": float(c)}
    if method == "triangulated":
        pts = np.column_stack([bx, by])
        base = np.full(shape, np.nan)
        lin = LinearNDInterpolator(pts, bz)
        base[inside] = lin(xx[inside], yy[inside])
        gaps = inside & np.isnan(base)
        if gaps.any():  # concave corners outside the hull of the samples
            base[gaps] = NearestNDInterpolator(pts, bz)(xx[gaps], yy[gaps])
        return base, None
    raise TerrainError(f"Unknown base method {method!r}.")


def compute_volume(
    ds: rasterio.io.DatasetReader,
    polygon_wgs84: BaseGeometry,
    method: BaseMethod = "triangulated",
    custom_elevation: float | None = None,
    other: rasterio.io.DatasetReader | None = None,
    slope_threshold_deg: float = 30.0,
) -> VolumeResult:
    if not isinstance(polygon_wgs84, (Polygon, MultiPolygon)) or polygon_wgs84.is_empty:
        raise TerrainError("Volumes need a Polygon or MultiPolygon.")
    if not polygon_wgs84.is_valid:
        polygon_wgs84 = shapely.make_valid(polygon_wgs84)
    ds_crs = CRS.from_user_input(ds.crs)
    poly = _to_crs(polygon_wgs84, WGS84, ds_crs)
    win = _read_window(ds, poly.bounds)
    rows, cols = win.z.shape

    inside = geometry_mask([poly], out_shape=(rows, cols), transform=win.transform, invert=True)
    if not inside.any():  # sliver polygons smaller than a cell
        inside = geometry_mask(
            [poly], out_shape=(rows, cols), transform=win.transform, invert=True, all_touched=True
        )
    if not inside.any():
        raise TerrainError("Geometry does not overlap the DEM.")

    xx, yy = _cell_centres(win.transform, rows, cols)
    boundary = _boundary_samples(poly, win) if method in BOUNDARY_METHODS else None
    base, plane = _base_surface(method, boundary, win, xx, yy, inside, custom_elevation, other, ds_crs)

    metrics = cell_metrics(ds_crs, win.transform, rows, cols)
    dx, dy, area = metrics.per_cell(cols)

    valid = inside & np.isfinite(win.z) & np.isfinite(base)
    n_inside, n_valid = int(inside.sum()), int(valid.sum())
    if n_valid == 0:
        raise TerrainError("The DEM has no valid elevations inside the polygon.")

    diff = np.where(valid, win.z - base, 0.0)
    cut = float(np.sum(np.clip(diff, 0, None) * area))
    fill = float(np.sum(np.clip(-diff, 0, None) * area))

    gx, gy = _slope_components(win.z, dx, dy)
    grad = np.hypot(gx, gy)
    grad_ok = valid & np.isfinite(grad)
    surface_factor = np.where(grad_ok, np.sqrt(1 + grad**2), 1.0)
    slope_deg = np.degrees(np.arctan(grad))

    warnings: list[str] = []
    coverage = 100.0 * n_valid / n_inside
    if coverage < 99.5:
        warnings.append(
            f"{100 - coverage:.1f}% of cells inside the polygon have no elevation (nodata) and were skipped."
        )
    relief = float(np.ptp(boundary[2])) if boundary is not None else None
    if method in ("lowest", "highest", "mean") and relief is not None and relief > 1.0:
        warnings.append(
            f"Boundary elevation varies by {relief:.1f} m. A flat '{method}' base on sloped ground "
            "mis-states the volume; 'plane' or 'triangulated' follow the terrain."
        )

    z_in = win.z[valid]
    base_in = base[valid]
    steep = slope_deg[grad_ok]
    return VolumeResult(
        base_method=method,
        base_elevation={
            "min": round(float(base_in.min()), 3),
            "max": round(float(base_in.max()), 3),
            "mean": round(float(base_in.mean()), 3),
            **({"boundary_relief_m": round(relief, 3)} if relief is not None else {}),
        },
        cut_m3=round(cut, 3),
        fill_m3=round(fill, 3),
        net_m3=round(cut - fill, 3),
        area_2d_m2=round(float(area[valid].sum()), 3),
        surface_area_3d_m2=round(float((area * surface_factor)[valid].sum()), 3),
        elevation={
            "min": round(float(z_in.min()), 3),
            "max": round(float(z_in.max()), 3),
            "mean": round(float(z_in.mean()), 3),
        },
        max_height_above_base_m=round(float(max(0.0, (z_in - base_in).max())), 3),
        max_depth_below_base_m=round(float(max(0.0, (base_in - z_in).max())), 3),
        slope={
            "mean_deg": round(float(steep.mean()), 2) if steep.size else 0.0,
            "max_deg": round(float(steep.max()), 2) if steep.size else 0.0,
            "threshold_deg": slope_threshold_deg,
            "steeper_than_threshold_pct": (
                round(float((steep > slope_threshold_deg).mean() * 100), 2) if steep.size else 0.0
            ),
        },
        cells={"inside": n_inside, "valid": n_valid, "nodata": n_inside - n_valid},
        coverage_pct=round(coverage, 2),
        cell_size_m=(round(float(metrics.dx_m.mean()), 4), round(float(metrics.dy_m.mean()), 4)),
        cell_method=metrics.method,
        plane=plane,
        warnings=warnings,
    )


# --------------------------------------------------------------------- profiles


@dataclass
class ProfileResult:
    distance_m: list[float]
    elevation_m: list[float | None]
    lon: list[float]
    lat: list[float]
    length_m: float
    stats: dict[str, Any]


def _densify_geodesic_line(
    line: LineString, step_m: float, max_samples: int
) -> tuple[FloatArray, FloatArray]:
    coords = np.asarray(line.coords)[:, :2]
    _, _, seg = GEOD.inv(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    total = float(np.sum(seg))
    # At most max_samples points in total (both ends included).
    step = max(step_m, total / (max_samples - 1)) if total > 0 else step_m
    lons, lats = [coords[0, 0]], [coords[0, 1]]
    for (x0, y0), (x1, y1), d in zip(coords[:-1], coords[1:], np.atleast_1d(seg), strict=True):
        n = max(0, math.ceil(d / step) - 1)  # interior points on this segment
        if n:
            for lon, lat in GEOD.npts(x0, y0, x1, y1, n):
                lons.append(lon)
                lats.append(lat)
        lons.append(x1)
        lats.append(y1)
    return np.asarray(lons), np.asarray(lats)


def compute_profile(
    ds: rasterio.io.DatasetReader, line_wgs84: BaseGeometry, max_samples: int = 1_000
) -> ProfileResult:
    if not isinstance(line_wgs84, LineString) or len(line_wgs84.coords) < 2:
        raise TerrainError("Profiles need a LineString with at least 2 positions.")
    max_samples = max(2, min(max_samples, MAX_PROFILE_SAMPLES))
    ds_crs = CRS.from_user_input(ds.crs)
    native = _to_crs(line_wgs84, WGS84, ds_crs)
    win = _read_window(ds, native.bounds)
    step_m = float(cell_metrics(ds_crs, win.transform, 1, 1).dx_m[0])

    lon, lat = _densify_geodesic_line(line_wgs84, step_m, max_samples)
    t = Transformer.from_crs(WGS84, ds_crs, always_xy=True)
    x, y = t.transform(lon, lat)
    z = _sample_bilinear(win.z, win.transform, np.asarray(x), np.asarray(y))
    _, _, seg = GEOD.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
    dist = np.concatenate([[0.0], np.cumsum(seg)])

    ok = np.isfinite(z)
    if ok.sum() < 2:
        raise TerrainError("The line does not cross enough valid DEM cells.")
    dz = np.diff(z)
    seg_ok = np.isfinite(dz) & (seg > 0)
    grades = np.zeros_like(seg)
    grades[seg_ok] = dz[seg_ok] / seg[seg_ok] * 100
    # Grade over a cell-sized window is noisy; report the steepest sustained grade
    # over ~5 cells as well, which is what matters for haul roads.
    win_n = max(1, min(5, len(seg) // 2))
    if seg_ok.sum() >= win_n:
        dz_c = np.where(seg_ok, dz, 0.0)
        seg_c = np.where(seg_ok, seg, 0.0)
        kernel = np.ones(win_n)
        run_dz = np.convolve(dz_c, kernel, mode="valid")
        run_len = np.convolve(seg_c, kernel, mode="valid")
        sustained = np.abs(np.divide(run_dz, run_len, out=np.zeros_like(run_dz), where=run_len > 0)) * 100
        max_sustained = float(sustained.max())
    else:
        max_sustained = float(np.abs(grades[seg_ok]).max()) if seg_ok.any() else 0.0

    zv = z[ok]
    return ProfileResult(
        distance_m=[round(float(d), 3) for d in dist],
        elevation_m=[round(float(v), 3) if np.isfinite(v) else None for v in z],
        lon=[round(float(v), 8) for v in lon],
        lat=[round(float(v), 8) for v in lat],
        length_m=round(float(dist[-1]), 3),
        stats={
            "samples": len(z),
            "nodata_samples": int((~ok).sum()),
            "min_m": round(float(zv.min()), 3),
            "max_m": round(float(zv.max()), 3),
            "start_m": round(float(zv[0]), 3),
            "end_m": round(float(zv[-1]), 3),
            "total_climb_m": round(float(np.sum(dz[seg_ok & (dz > 0)])), 3),
            "total_descent_m": round(float(-np.sum(dz[seg_ok & (dz < 0)])), 3),
            "mean_abs_grade_pct": round(float(np.abs(grades[seg_ok]).mean()), 2) if seg_ok.any() else 0.0,
            "max_sustained_grade_pct": round(max_sustained, 2),
            "sample_spacing_m": round(float(np.median(seg)), 3) if len(seg) else 0.0,
        },
    )


# --------------------------------------------------------------------- altitude


def sample_altitude(ds: rasterio.io.DatasetReader, lon: float, lat: float) -> float | None:
    ds_crs = CRS.from_user_input(ds.crs)
    x, y = Transformer.from_crs(WGS84, ds_crs, always_xy=True).transform(lon, lat)
    left, bottom, right, top = ds.bounds
    if not (left <= x <= right and bottom <= y <= top):
        return None
    pad = 2 * max(abs(ds.transform.a), abs(ds.transform.e))
    win = _read_window(ds, (x - pad, y - pad, x + pad, y + pad), pad=1)
    z = _sample_bilinear(win.z, win.transform, np.array([x]), np.array([y]))[0]
    return round(float(z), 3) if np.isfinite(z) else None
