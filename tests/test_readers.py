import io
import zipfile

import pytest
import shapefile

from app.errors import InvalidGeoFile, UnsupportedFileType
from app.geo.archive import ArchiveLimits
from app.geo.readers import read_dataset
from tests import factories as f

LIMITS = ArchiveLimits(max_members=100, max_uncompressed_bytes=10 * 1024 * 1024, max_compression_ratio=200)


def features(dataset):
    return [feat for layer in dataset.layers for feat in layer.features]


# --------------------------------------------------------------------------- KML


def test_kml_extracts_geometry_properties_ids_and_folders():
    body = (
        "<name>Survey</name><Folder><name>Plots</name>"
        + f.placemark(
            f.kml_polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]),
            name="Plot A",
            pid="plot-a",
            extra=(
                "<description>North field</description><ExtendedData>"
                '<Data name="owner"><value>Ravi</value></Data>'
                '<SchemaData schemaUrl="#s"><SimpleData name="crop">Ragi</SimpleData></SchemaData>'
                "</ExtendedData>"
            ),
        )
        + "</Folder>"
        + f.placemark("<Point><coordinates>1,2,3</coordinates></Point>", name="Tower")
    )
    ds = read_dataset("survey.kml", f.kml(body), LIMITS)
    assert ds.file_format == "kml"
    assert ds.layers[0].crs.to_epsg() == 4326
    poly, point = features(ds)

    assert poly.geometry.geom_type == "Polygon"
    assert poly.source_id == "plot-a"
    assert poly.layer == "Survey/Plots"
    assert poly.properties == {
        "name": "Plot A",
        "description": "North field",
        "owner": "Ravi",
        "crop": "Ragi",
    }

    assert point.geometry.geom_type == "Point"
    assert point.geometry.has_z
    assert point.layer == "Survey"


@pytest.mark.parametrize("ns", [None, "http://earth.google.com/kml/2.1", "http://www.opengis.net/kml/2.2"])
def test_kml_namespace_variants(ns):
    ds = read_dataset("a.kml", f.kml(f.placemark(f.kml_line([(0, 0), (1, 1)])), ns=ns), LIMITS)
    assert features(ds)[0].geometry.geom_type == "LineString"


def test_kml_polygon_holes_both_encodings():
    outer = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
    h1 = [(1, 1), (1, 2), (2, 2), (2, 1), (1, 1)]
    h2 = [(5, 5), (5, 6), (6, 6), (6, 5), (5, 5)]
    # One <innerBoundaryIs> per hole:
    separate = f.kml_polygon(outer, holes=[h1, h2])
    # Several rings inside a single <innerBoundaryIs>:
    ring = "<LinearRing><coordinates>{}</coordinates></LinearRing>"
    joined = f.kml_polygon(outer).replace(
        "</Polygon>",
        "<innerBoundaryIs>"
        + "".join(ring.format(" ".join(f"{x},{y}" for x, y in h)) for h in (h1, h2))
        + "</innerBoundaryIs></Polygon>",
    )
    for geom_xml in (separate, joined):
        (feat,) = features(read_dataset("a.kml", f.kml(f.placemark(geom_xml)), LIMITS))
        assert len(feat.geometry.interiors) == 2


def test_kml_unclosed_ring_is_closed():
    xml = f.kml_polygon([(0, 0), (0, 1), (1, 1), (1, 0)])  # last != first
    (feat,) = features(read_dataset("a.kml", f.kml(f.placemark(xml)), LIMITS))
    assert feat.geometry.is_valid


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["<Point><coordinates>0,0</coordinates></Point>"] * 2, "MultiPoint"),
        ([f.kml_line([(0, 0), (1, 1)])] * 2, "MultiLineString"),
        ([f.kml_polygon([(0, 0), (0, 1), (1, 1), (0, 0)])] * 2, "MultiPolygon"),
        (
            ["<Point><coordinates>0,0</coordinates></Point>", f.kml_line([(0, 0), (1, 1)])],
            "GeometryCollection",
        ),
    ],
)
def test_kml_multigeometry_collapses_to_specific_type(parts, expected):
    xml = f"<MultiGeometry>{''.join(parts)}</MultiGeometry>"
    (feat,) = features(read_dataset("a.kml", f.kml(f.placemark(xml)), LIMITS))
    assert feat.geometry.geom_type == expected


def test_kml_bad_feature_does_not_break_the_file():
    body = (
        f.placemark("<LineString><coordinates>0,0 abc,1</coordinates></LineString>", name="bad")
        + f.placemark("<LineString><coordinates>0,0</coordinates></LineString>", name="too-short")
        + f.placemark("<Model><Link><href>tree.dae</href></Link></Model>", name="model")
        + "<Placemark><name>no-geom</name></Placemark>"
        + f.placemark(f.kml_line([(0, 0), (1, 1)]), name="good")
    )
    bad, short, model, nogeom, good = features(read_dataset("a.kml", f.kml(body), LIMITS))
    assert bad.geometry is None
    assert "Invalid coordinate" in bad.error
    assert short.geometry is None
    assert "at least 2" in short.error
    assert model.unsupported
    assert model.declared_type == "Model"
    assert nogeom.geometry is None
    assert nogeom.declared_type is None
    assert good.geometry.geom_type == "LineString"


def test_kml_deep_nesting_does_not_crash():
    depth = 5_000
    body = "<Folder>" * depth + f.placemark(f.kml_line([(0, 0), (1, 1)])) + "</Folder>" * depth
    (feat,) = features(read_dataset("a.kml", f.kml(body), LIMITS))
    assert feat.geometry.geom_type == "LineString"

    deep_geom = "<MultiGeometry>" * depth + "</MultiGeometry>" * depth
    (feat,) = features(read_dataset("a.kml", f.kml(f.placemark(deep_geom)), LIMITS))
    assert feat.geometry is None
    assert "too deeply" in feat.error


def test_kml_tolerates_spaces_after_commas():
    xml = "<LineString><coordinates>0, 0  1, 1</coordinates></LineString>"
    (feat,) = features(read_dataset("a.kml", f.kml(f.placemark(xml)), LIMITS))
    assert list(feat.geometry.coords) == [(0, 0), (1, 1)]


def test_kml_rejects_entity_expansion():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><kml><Document><name>&b;</name></Document></kml>'
    )
    with pytest.raises(InvalidGeoFile, match="forbidden"):
        read_dataset("bomb.kml", bomb, LIMITS)


def test_kml_rejects_external_entities():
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<kml><Document><name>&x;</name></Document></kml>"
    )
    with pytest.raises(InvalidGeoFile):
        read_dataset("xxe.kml", xxe, LIMITS)


@pytest.mark.parametrize(
    ("content", "match"),
    [(b"not xml at all", "well-formed"), (b"<gpx></gpx>", "expected <kml>"), (b"", "empty")],
)
def test_kml_invalid_documents(content, match):
    with pytest.raises(InvalidGeoFile, match=match):
        read_dataset("a.kml", content, LIMITS)


def test_kmz_reads_root_doc_kml():
    doc = f.kml(f.placemark(f.kml_line([(0, 0), (1, 1)])))
    data = f.zip_bytes({"doc.kml": doc, "files/overlay.png": b"\x89PNG"})
    ds = read_dataset("a.kmz", data, LIMITS)
    assert ds.file_format == "kmz"
    assert len(features(ds)) == 1


def test_kmz_without_kml():
    with pytest.raises(InvalidGeoFile, match=r"does not contain a .kml"):
        read_dataset("a.kmz", f.zip_bytes({"x.txt": "hi"}), LIMITS)


# --------------------------------------------------------------------- Shapefile


def test_shapefile_polygons_with_crs_and_attributes():
    data = f.shapefile_zip(
        shape_type=shapefile.POLYGON,
        geometries=[f.square(77.59, 12.97, 0.01), f.square(77.61, 12.97, 0.01)],
        records=[
            {"name": "A", "area": 1.5, "surveyed": f.TODAY},
            {"name": "Ä-ü", "area": None, "surveyed": None},
        ],
        fields=[("name", "C"), ("area", "N"), ("surveyed", "D")],
        epsg=4326,
    )
    ds = read_dataset("parcels.zip", data, LIMITS)
    (layer,) = ds.layers
    assert ds.file_format == "shapefile"
    assert layer.name == "parcels"
    assert layer.crs.to_epsg() == 4326
    a, b = features(ds)
    assert a.geometry.geom_type == "Polygon"
    assert a.properties == {"name": "A", "area": 1.5, "surveyed": "2024-01-15"}
    assert b.properties["name"] == "Ä-ü"
    assert a.layer == "parcels"


def test_shapefile_in_nested_folder_with_macos_junk():
    parts = f.shapefile_parts(shape_type=shapefile.POINT, geometries=[(1, 2)])
    members = {f"export/data/pts{ext}": v for ext, v in parts.items()}
    members["__MACOSX/export/data/._pts.shp"] = b"junk"
    members["export/data/pts.PRJ"] = f.prj(4326)  # upper-case extension
    ds = read_dataset("pts.zip", f.zip_bytes(members), LIMITS)
    assert ds.layers[0].crs.to_epsg() == 4326
    assert features(ds)[0].geometry.geom_type == "Point"


def test_shapefile_multiple_layers():
    polys = f.shapefile_parts(shape_type=shapefile.POLYGON, geometries=[f.square(0, 0, 1)])
    lines = f.shapefile_parts(shape_type=shapefile.POLYLINE, geometries=[[[(0, 0), (1, 1)]]])
    members = {f"parcels{e}": v for e, v in polys.items()} | {f"roads{e}": v for e, v in lines.items()}
    members["parcels.prj"] = f.prj(4326)
    members["roads.prj"] = f.prj(32643)
    ds = read_dataset("bundle.zip", f.zip_bytes(members), LIMITS)
    assert [layer.name for layer in ds.layers] == ["parcels", "roads"]
    assert [layer.crs.to_epsg() for layer in ds.layers] == [4326, 32643]


def test_shapefile_polygon_holes_from_ring_orientation():
    outer = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]  # clockwise
    hole = [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]  # counter-clockwise
    data = f.shapefile_zip(shape_type=shapefile.POLYGON, geometries=[[outer, hole]])
    (feat,) = features(read_dataset("a.zip", data, LIMITS))
    assert feat.geometry.geom_type == "Polygon"
    assert len(feat.geometry.interiors) == 1


def test_shapefile_null_shape_is_kept_as_feature():
    data = f.shapefile_zip(shape_type=shapefile.POLYGON, geometries=[f.square(0, 0, 1), None])
    ok, null = features(read_dataset("a.zip", data, LIMITS))
    assert ok.geometry is not None
    assert null.geometry is None
    assert null.properties == {"name": "f1"}


def test_shapefile_without_prj_warns():
    data = f.shapefile_zip(shape_type=shapefile.POINT, geometries=[(1, 2)], epsg=None)
    ds = read_dataset("a.zip", data, LIMITS)
    assert ds.layers[0].crs is None
    assert "no .prj" in ds.layers[0].warnings[0]


def test_shapefile_with_garbage_prj_warns():
    parts = f.shapefile_parts(shape_type=shapefile.POINT, geometries=[(1, 2)])
    members = {f"a{e}": v for e, v in parts.items()} | {"a.prj": "this is not WKT"}
    ds = read_dataset("a.zip", f.zip_bytes(members), LIMITS)
    assert ds.layers[0].crs is None
    assert "could not be parsed" in ds.layers[0].warnings[0]


def test_shapefile_cpg_encoding():
    parts = f.shapefile_parts(
        shape_type=shapefile.POINT, geometries=[(1, 2)], records=[{"name": "Málaga"}], encoding="latin-1"
    )
    members = {f"a{e}": v for e, v in parts.items()} | {"a.cpg": "ISO-8859-1"}
    (feat,) = features(read_dataset("a.zip", f.zip_bytes(members), LIMITS))
    assert feat.properties["name"] == "Málaga"


@pytest.mark.parametrize("missing", [".dbf", ".shx"])
def test_shapefile_missing_required_part(missing):
    data = f.shapefile_zip(shape_type=shapefile.POINT, geometries=[(1, 2)], drop=[missing])
    with pytest.raises(InvalidGeoFile, match=f"missing required part.*{missing}"):
        read_dataset("a.zip", data, LIMITS)


def test_zip_without_shapefile():
    with pytest.raises(InvalidGeoFile, match=r"does not contain a .shp"):
        read_dataset("a.zip", f.zip_bytes({"readme.txt": "hi"}), LIMITS)


def test_corrupt_shp_content():
    parts = f.shapefile_parts(shape_type=shapefile.POINT, geometries=[(1, 2)])
    members = {f"a{e}": v for e, v in parts.items()}
    members["a.shp"] = b"\x00" * 50
    with pytest.raises(InvalidGeoFile):
        features(read_dataset("a.zip", f.zip_bytes(members), LIMITS))


# ------------------------------------------------------------- Format / archives


@pytest.mark.parametrize("name", ["a.geojson", "a.shp", "a", "a.zip.exe"])
def test_unsupported_extensions(name):
    with pytest.raises(UnsupportedFileType):
        read_dataset(name, b"x", LIMITS)


def test_extension_content_mismatch():
    with pytest.raises(InvalidGeoFile, match="not a zip"):
        read_dataset("a.zip", b"<kml/>", LIMITS)
    with pytest.raises(InvalidGeoFile, match="is a zip"):
        read_dataset("a.kml", f.zip_bytes({"a.kml": "x"}), LIMITS)


def test_zip_bomb_by_ratio_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.shp", b"\x00" * (5 * 1024 * 1024))  # compresses ~1000:1
    with pytest.raises(InvalidGeoFile, match="compression ratio"):
        read_dataset("a.zip", buf.getvalue(), LIMITS)


def test_zip_too_large_uncompressed_is_rejected():
    small = ArchiveLimits(max_members=10, max_uncompressed_bytes=1000, max_compression_ratio=10_000)
    data = f.zip_bytes({"a.shp": b"x" * 2000})
    with pytest.raises(InvalidGeoFile, match="too large"):
        read_dataset("a.zip", data, small)


def test_zip_too_many_members():
    small = ArchiveLimits(max_members=2, max_uncompressed_bytes=10_000, max_compression_ratio=10_000)
    with pytest.raises(InvalidGeoFile, match="too many files"):
        read_dataset("a.zip", f.zip_bytes({f"{i}.txt": "x" for i in range(3)}), small)


def test_corrupt_zip():
    with pytest.raises(InvalidGeoFile, match="not a valid zip"):
        read_dataset("a.zip", b"PK\x03\x04garbage", LIMITS)
