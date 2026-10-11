"""Metric cell geometry for elevation rasters.

Volume = sum(height * cell area), so cell area has to be right in square metres,
whatever CRS the DEM uses:

* Projected CRS (e.g. UTM): cell size in CRS units * unit-to-metre factor, divided
  by the projection's local scale factors (pyproj ``Proj.get_factors``) at the
  site. A 0.25 m UTM cell 277 km from the central meridian is really 0.2497 m on
  the ground; that 0.11% would otherwise flow straight into every volume.
* Geographic CRS (lon/lat): a cell is a fixed angle, so its ground size shrinks
  with latitude. Width and height are computed per row as geodesic distances on
  the WGS84 ellipsoid.

Only north-up rasters are accepted; rotated or sheared geotransforms are rejected
at upload.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from affine import Affine
from pyproj import CRS, Geod, Proj, Transformer

GEOD = Geod(ellps="WGS84")
FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class CellMetrics:
    """Ground size of the cells in each row of a raster window (metres)."""

    dx_m: FloatArray  # shape (rows,): cell width
    dy_m: FloatArray  # shape (rows,): cell height
    method: str

    @property
    def area_m2(self) -> FloatArray:
        return self.dx_m * self.dy_m

    def per_cell(self, cols: int) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Broadcast row values to a (rows, cols) grid."""
        dx = np.repeat(self.dx_m[:, None], cols, axis=1)
        dy = np.repeat(self.dy_m[:, None], cols, axis=1)
        return dx, dy, dx * dy


def is_north_up(transform: Affine) -> bool:
    return transform.b == 0 and transform.d == 0 and transform.a > 0 and transform.e < 0


def cell_metrics(crs: CRS, transform: Affine, rows: int, cols: int) -> CellMetrics:
    """Ground cell sizes for a ``rows`` x ``cols`` window starting at ``transform``'s origin."""
    res_x, res_y = transform.a, -transform.e
    row_centres_y = transform.f + transform.e * (np.arange(rows) + 0.5)

    if crs.is_geographic:
        lat = row_centres_y
        lon0 = np.full(rows, transform.c)
        _, _, dx = GEOD.inv(lon0, lat, lon0 + res_x, lat)
        _, _, dy = GEOD.inv(lon0, lat - res_y / 2, lon0, lat + res_y / 2)
        return CellMetrics(np.asarray(dx, dtype=float), np.asarray(dy, dtype=float), "geodesic per row")

    unit = crs.axis_info[0].unit_conversion_factor if crs.axis_info else 1.0
    # Local scale factors at the window centre: projections stretch or shrink distance.
    cx = transform.c + transform.a * 0.5 * max(cols, 1)
    cy = transform.f + transform.e * 0.5 * max(rows, 1)
    lon, lat = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True).transform(cx, cy)
    try:
        factors = Proj(crs).get_factors(lon, lat)
        k_x, k_y = factors.parallel_scale, factors.meridional_scale
        method = f"projected, scale-corrected (k={k_x:.6f})"
        if not (np.isfinite(k_x) and np.isfinite(k_y) and k_x > 0 and k_y > 0):
            raise ValueError
    except Exception:  # some CRSs don't expose factors; fall back to nominal size
        k_x = k_y = 1.0
        method = "projected, nominal cell size"
    dx = np.full(rows, res_x * unit / k_x)
    dy = np.full(rows, res_y * unit / k_y)
    return CellMetrics(dx, dy, method)
