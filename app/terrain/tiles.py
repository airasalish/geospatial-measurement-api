"""Web-map tiles rendered from DEM COGs (XYZ / Web Mercator).

rio-tiler reads only the COG blocks (or overview levels) needed for a tile, and
we shade them here:

* ``hillshade``: Horn's method, sun at 315° azimuth and 45° altitude, computed
  with each tile's true ground pixel size so relief looks right at every zoom.
* ``elevation``: hypsometric tint between the DEM's min and max, multiplied by
  the hillshade.
* ``slope``: slope classes for stability and risk review (<10°, 10-20°, 20-30°,
  30-45°, >45°) over the hillshade.

A one-cell buffer around each tile keeps the gradients continuous across tile seams.
"""

import math
from typing import Literal

import numpy as np
import numpy.typing as npt
import rasterio
from morecantile import Tile, tms
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader
from rio_tiler.utils import render

Style = Literal["hillshade", "elevation", "slope"]
TILE = 256
WEB_MERCATOR = tms.get("WebMercatorQuad")
EARTH_CIRCUMFERENCE = 2 * math.pi * 6_378_137

# Hypsometric palette (low -> high): valley green, olive, sand, ochre, light stone.
_HYPSO = np.array(
    [[47, 93, 58], [122, 154, 84], [214, 194, 122], [176, 122, 70], [242, 235, 224]], dtype=np.float64
)
# Slope classes: upper bound (deg) -> RGB.
_SLOPE_CLASSES = [
    (10, (126, 176, 101)),
    (20, (233, 211, 99)),
    (30, (238, 146, 61)),
    (45, (214, 64, 46)),
    (90, (122, 22, 32)),
]


def _pixel_size_m(z: int, x: int, y: int) -> float:
    b = WEB_MERCATOR.bounds(Tile(x, y, z))
    lat = math.radians((b.top + b.bottom) / 2)
    return float(EARTH_CIRCUMFERENCE * math.cos(lat) / (TILE * 2**z))


def _shade(
    dem: npt.NDArray[np.float64], px: float
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Hillshade (0..1) and slope (deg) for a buffered elevation tile."""
    filled = np.where(np.isnan(dem), np.nanmean(dem) if np.isfinite(dem).any() else 0.0, dem)
    gy, gx = np.gradient(filled, px)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gy, gx)
    azimuth, altitude = math.radians(315), math.radians(45)
    zenith = math.pi / 2 - altitude
    shade = math.cos(zenith) * np.cos(slope) + math.sin(zenith) * np.sin(slope) * np.cos(
        azimuth - math.pi / 2 - aspect
    )
    return np.clip(shade, 0, 1), np.degrees(slope)


def _hypsometric(t: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    pos = np.clip(np.nan_to_num(t), 0, 1) * (len(_HYPSO) - 1)
    lo = np.floor(pos).astype(int).clip(0, len(_HYPSO) - 2)
    frac = (pos - lo)[..., None]
    rgb: npt.NDArray[np.float64] = _HYPSO[lo] * (1 - frac) + _HYPSO[lo + 1] * frac
    return rgb


def render_tile(
    path: str,
    z: int,
    x: int,
    y: int,
    style: Style,
    z_range: tuple[float, float],
    gdal_env: dict[str, str] | None = None,
) -> bytes | None:
    """PNG bytes for tile z/x/y, or None when the tile is outside the DEM."""
    with rasterio.Env(**(gdal_env or {})), Reader(path) as src:
        if not src.tile_exists(x, y, z):
            return None
        try:
            # Cubic for both zoom resampling and the UTM -> Web Mercator reprojection.
            # The reprojection defaults to nearest neighbour, which stair-steps the
            # surface and shows up as a grid in hillshade and slope.
            img = src.tile(
                x,
                y,
                z,
                tilesize=TILE,
                indexes=1,
                buffer=1,
                resampling_method="cubic",
                reproject_method="cubic",
            )
        except TileOutsideBounds:
            return None
    band = img.array[0]
    dem = np.ma.filled(band.astype(np.float64), np.nan)
    valid = ~np.ma.getmaskarray(band) & np.isfinite(dem)
    if not valid.any():
        return None

    shade, slope = _shade(dem, _pixel_size_m(z, x, y))
    # Drop the 1-pixel buffer.
    inner = (slice(1, -1), slice(1, -1))
    shade, slope, dem, valid = shade[inner], slope[inner], dem[inner], valid[inner]

    if style == "hillshade":
        grey = 60 + 195 * shade
        rgb = np.stack([grey, grey, grey * 0.97], axis=-1)
    elif style == "slope":
        rgb = np.zeros((*slope.shape, 3))
        lower = 0.0
        for upper, colour in _SLOPE_CLASSES:
            rgb[(slope >= lower) & (slope < upper)] = colour
            lower = upper
        rgb *= (0.55 + 0.45 * shade)[..., None]
    else:
        z_min, z_max = z_range
        span = max(z_max - z_min, 1e-6)
        t = np.asarray((dem - z_min) / span, dtype=np.float64)
        rgb = _hypsometric(t) * (0.5 + 0.5 * shade)[..., None]

    data = np.clip(np.nan_to_num(rgb), 0, 255).astype(np.uint8).transpose(2, 0, 1)
    mask = np.where(valid, 255, 0).astype(np.uint8)
    return render(data, mask, img_format="PNG")


def tile_zoom_range(path: str, gdal_env: dict[str, str] | None = None) -> tuple[int, int]:
    with rasterio.Env(**(gdal_env or {})), Reader(path) as src:
        return int(src.minzoom), int(src.maxzoom)
