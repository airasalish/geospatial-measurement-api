"""Generate the sample files in ``samples/``.

python -m scripts.make_samples
"""

import math
import random
from pathlib import Path

import shapefile

from tests import factories as f

OUT = Path(__file__).resolve().parent.parent / "samples"


def _ring(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def survey_kml() -> bytes:
    """A small drone-survey style KML around Bengaluru exercising every geometry path."""
    plot = _ring(77.5946, 12.9716, 77.5966, 12.9731)
    pond = _ring(77.5952, 12.9721, 77.5958, 12.9726)
    road = [(77.5940, 12.9710), (77.5955, 12.9712), (77.5972, 12.9709)]
    fence_a = [(77.5946, 12.9735), (77.5966, 12.9735)]
    fence_b = [(77.5970, 12.9716), (77.5970, 12.9731)]
    ext = (
        '<ExtendedData><Data name="survey_date"><value>2024-03-14</value></Data>'
        '<Data name="surveyor"><value>Team A</value></Data></ExtendedData>'
    )
    body = (
        "<name>Site 42 survey</name>"
        "<Folder><name>Boundaries</name>"
        + f.placemark(f.kml_polygon(plot, holes=[pond]), name="Plot 1 (with pond)", pid="plot-1", extra=ext)
        + "</Folder><Folder><name>Linear</name>"
        + f.placemark(f.kml_line(road), name="Access road", pid="road-1")
        + f.placemark(
            "<MultiGeometry>" + f.kml_line(fence_a) + f.kml_line(fence_b) + "</MultiGeometry>",
            name="Fence", pid="fence-1",
        )
        + "</Folder><Folder><name>Assets</name>"
        + f.placemark(
            "<Point><coordinates>77.5956,12.9723,0</coordinates></Point>", name="GCP-1", pid="gcp-1"
        )
        + f.placemark("<Model><Link><href>tower.dae</href></Link></Model>", name="Tower model", pid="tower-1")
        + "</Folder>"
    )  # fmt: skip
    return f.kml(body)


def parcels_utm_zip() -> bytes:
    """Shapefile in a projected CRS (UTM 43N, EPSG:32643) with attribute types."""
    x0, y0 = 777_000, 1_435_000

    def rect(x: float, y: float, w: float, h: float) -> list[list[tuple[float, float]]]:
        return [[(x, y), (x, y + h), (x + w, y + h), (x + w, y), (x, y)]]  # clockwise

    return f.shapefile_zip(
        stem="parcels",
        folder="parcels/",
        epsg=32643,
        shape_type=shapefile.POLYGON,
        geometries=[rect(x0, y0, 100, 100), rect(x0 + 150, y0, 200, 50), None],
        records=[
            {"parcel_id": "P-001", "owner": "Ravi", "surveyed": f.TODAY},
            {"parcel_id": "P-002", "owner": "Meera", "surveyed": f.TODAY},
            {"parcel_id": "P-003", "owner": "(no geometry)", "surveyed": None},
        ],
        fields=[("parcel_id", "C"), ("owner", "C"), ("surveyed", "D")],
    )


def farm_plots_zip() -> bytes:
    """A multi-layer survey bundle near Devanahalli (north of Bengaluru), in WGS84.

    plots  - ~430 irregular agricultural parcels on a jittered grid
    tracks - farm tracks running between the parcel rows
    wells  - borewell locations
    """
    rng = random.Random(7)  # noqa: S311  (deterministic sample data)
    lon0, lat0 = 77.700, 13.240
    m_lon = 1 / (111_320 * math.cos(math.radians(lat0)))  # degrees per metre
    m_lat = 1 / 110_574
    cols, rows, cell = 24, 18, 70.0  # metres

    def jitter(i: int, j: int) -> tuple[float, float]:
        # Shared corner jitter so neighbouring plots meet without gaps or overlaps.
        r = random.Random(i * 1000 + j)  # noqa: S311
        x = i * cell + (r.uniform(-14, 14) if 0 < i < cols else 0)
        y = j * cell + (r.uniform(-14, 14) if 0 < j < rows else 0)
        return lon0 + x * m_lon, lat0 + y * m_lat

    crops = ["Ragi", "Grapes", "Maize", "Tomato", "Fallow", "Mulberry", "Groundnut"]
    owners = ["Gowda", "Reddy", "Naik", "Shetty", "Rao", "Kumar", "Hegde", "Patil"]
    plots, records = [], []
    for i in range(cols):
        for j in range(rows):
            if rng.random() < 0.012:  # a few gaps: ponds, homesteads
                continue
            a, b, c, d = jitter(i, j), jitter(i + 1, j), jitter(i + 1, j + 1), jitter(i, j + 1)
            plots.append([[a, d, c, b, a]])  # clockwise
            records.append({
                "plot_no": f"DVH-{i:02d}{j:02d}",
                "owner": f"{rng.choice(owners)} {chr(65 + rng.randrange(26))}.",
                "crop": rng.choice(crops),
                "surveyed": f.TODAY,
            })  # fmt: skip

    tracks, track_recs = [], []
    for j in (6, 12):
        tracks.append([[jitter(i, j) for i in range(cols + 1)]])
        track_recs.append({"name": f"Row track {j // 6}", "surface": "Gravel"})
    tracks.append([[jitter(12, j) for j in range(rows + 1)]])
    track_recs.append({"name": "Spine road", "surface": "Tar"})

    wells = [jitter(rng.randrange(cols), rng.randrange(rows)) for _ in range(14)]
    well_recs = [
        {"well_id": f"BW-{k + 1:02d}", "depth_m": round(rng.uniform(180, 420), 1)} for k in range(14)
    ]

    members: dict[str, bytes | str] = {}
    for stem, kwargs in {
        "plots": dict(shape_type=shapefile.POLYGON, geometries=plots, records=records,
                      fields=[("plot_no", "C"), ("owner", "C"), ("crop", "C"), ("surveyed", "D")]),
        "tracks": dict(shape_type=shapefile.POLYLINE, geometries=tracks, records=track_recs,
                       fields=[("name", "C"), ("surface", "C")]),
        "wells": dict(shape_type=shapefile.POINT, geometries=wells, records=well_recs,
                      fields=[("well_id", "C"), ("depth_m", "N")]),
    }.items():  # fmt: skip
        for ext, data in f.shapefile_parts(**kwargs).items():
            members[f"devanahalli_survey/{stem}{ext}"] = data
        members[f"devanahalli_survey/{stem}.prj"] = f.prj(4326)
    return f.zip_bytes(members)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "site_survey.kml").write_bytes(survey_kml())
    (OUT / "parcels_utm43n.zip").write_bytes(parcels_utm_zip())
    (OUT / "farm_plots.zip").write_bytes(farm_plots_zip())
    print(f"wrote samples to {OUT}")


if __name__ == "__main__":
    main()
