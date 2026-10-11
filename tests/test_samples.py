"""The shipped samples give the results documented in samples/README.md."""

from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.mark.parametrize(
    ("name", "crs", "count", "area_m2", "length_m", "by_status"),
    [
        ("site_survey.kml", "EPSG:4326", 5, 32_408.162, 734.589,
         {"MEASURED": 3, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}),
        ("farm_plots.zip", "EPSG:4326", 442, 2_086_093.028, 4_684.677,
         {"MEASURED": 428, "NOT_APPLICABLE": 14}),
        ("parcels_utm43n.zip", "EPSG:32643", 3, 19_978.005, 0.0, {"MEASURED": 2, "NOT_APPLICABLE": 1}),
        ("quarry_outlines.kml", "EPSG:4326", 6, 17_547.110, 375.417, {"MEASURED": 6}),
    ],
)  # fmt: skip
def test_sample_results_match_the_docs(client, name, crs, count, area_m2, length_m, by_status):
    body = client.post("/api/files/", files={"file": (name, (SAMPLES / name).read_bytes())}).json()
    assert (body["status"], body["crs"], body["feature_count"]) == ("COMPLETED", crs, count)
    summary = client.get(f"/api/files/{body['id']}/measurements/").json()["summary"]
    assert summary["total_area_m2"] == pytest.approx(area_m2, abs=0.01)
    assert summary["total_length_m"] == pytest.approx(length_m, abs=0.01)
    assert summary["by_status"] == by_status
