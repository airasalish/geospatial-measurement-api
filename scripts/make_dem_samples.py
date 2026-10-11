"""Generate the terrain samples in ``samples/``: two surveys of a quarry yard plus outlines.

    python -m scripts.make_dem_samples

Every feature has an analytic volume, so the samples double as an accuracy check:

| feature                  | shape                          | volume formula             |
|--------------------------|--------------------------------|----------------------------|
| Coal stockpile           | cone r=28 m, h=11 m            | pi r^2 h / 3               |
| Iron-ore fines           | frustum R=32, r=12 m, h=9 m    | pi h (R^2 + R r + r^2) / 3 |
| Overburden dump          | elliptic cone a=60, b=20, h=8  | pi a b h / 3               |
| Sand pile                | cone r=12 m, h=5 m             | pi r^2 h / 3               |
| Excavation (pit)         | inverted frustum, depth 7 m    | pi d (R^2 + R r + r^2) / 3 |

Shapes are defined in UTM grid metres; true ground volumes are smaller by the
UTM scale factor squared (~0.1% here), which the API accounts for.
"""

import math
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.affinity import rotate, scale
from shapely.geometry import LineString, Point

from tests import factories as f

OUT = Path(__file__).resolve().parent.parent / "samples"
CRS = "EPSG:32643"
RES = 0.5
SIZE = 400.0  # metres
SITE_LON, SITE_LAT = 77.725, 13.245


def _site_origin() -> tuple[float, float, float, float]:
    cx, cy = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(SITE_LON, SITE_LAT)
    cx, cy = round(cx), round(cy)
    return cx, cy, cx - SIZE / 2, cy + SIZE / 2


def _cone(u, v, r, h):
    return np.clip(h * (1 - np.hypot(u, v) / r), 0, None)


def _frustum(u, v, r_base, r_top, h):
    d = np.hypot(u, v)
    return np.where(d <= r_top, h, np.clip(h * (r_base - d) / (r_base - r_top), 0, None))


def _elliptic_cone(u, v, a, b, h, angle_deg):
    t = math.radians(angle_deg)
    uu = u * math.cos(t) + v * math.sin(t)
    vv = -u * math.sin(t) + v * math.cos(t)
    return np.clip(h * (1 - np.hypot(uu / a, vv / b)), 0, None)


def surveys() -> tuple[np.ndarray, np.ndarray, dict]:
    cx, cy, x0, y0 = _site_origin()
    n = int(SIZE / RES)
    xs = x0 + RES * (np.arange(n) + 0.5)
    ys = y0 - RES * (np.arange(n) + 0.5)
    x, y = np.meshgrid(xs, ys)
    u, v = x - cx, y - cy  # metres from site centre, north up

    rng = np.random.default_rng(7)
    ground = (
        915.0
        - 0.03 * u
        - 0.02 * v  # ~3.6% fall towards the south-east
        + 0.8 * np.sin(u / 47.0) * np.cos(v / 61.0)
        + 0.5 * np.sin((u + v) / 33.0)
    )
    noise = rng.normal(0, 0.02, ground.shape)  # ~2 cm survey noise

    piles = {
        "Coal stockpile": dict(fn=lambda h=11.0, r=28.0: _cone(u + 80, v - 90, r, h), at=(-80, 90)),
        "Iron-ore fines": dict(fn=lambda: _frustum(u - 90, v - 80, 32.0, 12.0, 9.0), at=(90, 80)),
        "Overburden dump": dict(fn=lambda: _elliptic_cone(u - 60, v + 90, 60.0, 20.0, 8.0, 30), at=(60, -90)),
        "Sand pile": dict(fn=lambda: _cone(u + 40, v + 30, 12.0, 5.0), at=(-40, -30)),
    }
    pit = lambda depth=7.0: -_frustum(u + 110, v + 110, 35.0, 15.0, depth)  # noqa: E731

    after = ground + piles["Coal stockpile"]["fn"]() + piles["Iron-ore fines"]["fn"]()
    after += piles["Overburden dump"]["fn"]() + piles["Sand pile"]["fn"]() + pit() + noise
    # Earlier survey: smaller coal pile, no ore fines yet, shallower pit.
    before = ground + piles["Coal stockpile"]["fn"](h=7.0, r=22.0) + piles["Overburden dump"]["fn"]()
    before += piles["Sand pile"]["fn"]() + pit(depth=4.0) + rng.normal(0, 0.02, ground.shape)

    expected = {
        "Coal stockpile": math.pi * 28**2 * 11 / 3,
        "Iron-ore fines": math.pi * 9 * (32**2 + 32 * 12 + 12**2) / 3,
        "Overburden dump": math.pi * 60 * 20 * 8 / 3,
        "Sand pile": math.pi * 12**2 * 5 / 3,
        "Excavation": math.pi * 7 * (35**2 + 35 * 15 + 15**2) / 3,
    }
    geo = {"origin": (x0, y0), "centre": (cx, cy)}
    return after.astype(np.float32), before.astype(np.float32), {"expected": expected, **geo}


def _geotiff(z: np.ndarray, origin: tuple[float, float]) -> bytes:
    with MemoryFile() as mf:
        with mf.open(
            driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32", crs=CRS,
            transform=from_origin(origin[0], origin[1], RES, RES), nodata=-9999.0,
            tiled=True, blockxsize=256, blockysize=256, compress="deflate", predictor=3,
        ) as ds:  # fmt: skip
            ds.write(z, 1)
        return mf.read()


def outlines_kml(centre: tuple[float, float]) -> bytes:
    cx, cy = centre
    to84 = f.utm_to_wgs84

    def ring(geom):
        return list(to84(geom).exterior.coords)

    dump = rotate(scale(Point(cx + 60, cy - 90).buffer(1, 16), 64, 24), 30, origin=(cx + 60, cy - 90))
    shapes = [
        ("Coal stockpile", "pile-coal", Point(cx - 80, cy + 90).buffer(32, 16)),
        ("Iron-ore fines", "pile-ore", Point(cx + 90, cy + 80).buffer(36, 16)),
        ("Overburden dump", "dump-ob", dump),
        ("Sand pile", "pile-sand", Point(cx - 40, cy - 30).buffer(15, 16)),
        ("Excavation", "pit-1", Point(cx - 110, cy - 110).buffer(39, 16)),
    ]
    body = "<name>Quarry yard survey</name><Folder><name>Stockpiles</name>"
    for name, pid, geom in shapes:
        body += f.placemark(f.kml_polygon(ring(geom)), name=name, pid=pid)
    road = to84(LineString([(cx - 185, cy - 20), (cx - 20, cy + 10), (cx + 185, cy + 45)]))
    body += "</Folder><Folder><name>Haul roads</name>"
    body += f.placemark(f.kml_line(list(road.coords)), name="Main haul road", pid="road-1")
    body += "</Folder>"
    return f.kml(body)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    after, before, meta = surveys()
    (OUT / "quarry_dsm_2026-03.tif").write_bytes(_geotiff(after, meta["origin"]))
    (OUT / "quarry_dsm_2026-01.tif").write_bytes(_geotiff(before, meta["origin"]))
    (OUT / "quarry_outlines.kml").write_bytes(outlines_kml(meta["centre"]))
    for name, v in meta["expected"].items():
        print(f"{name:16s} analytic {v:12,.1f} m3")
    print(f"wrote terrain samples to {OUT}")


if __name__ == "__main__":
    main()
