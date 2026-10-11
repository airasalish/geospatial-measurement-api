"""Generate the sample files in ``samples/``.

python -m scripts.make_samples
"""

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


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "site_survey.kml").write_bytes(survey_kml())
    (OUT / "parcels_utm43n.zip").write_bytes(parcels_utm_zip())
    print(f"wrote samples to {OUT}")


if __name__ == "__main__":
    main()
