import { describe, expect, it } from "vitest";
import type { MapCollection, MapFeatureProps } from "./api";
import { baseName, measurementsCsv } from "./exporters";
import { geometryBounds } from "./geo";

const props = (over: Partial<MapFeatureProps>): MapFeatureProps => ({
  index: 0,
  name: null,
  geometry_type: "Polygon",
  status: "MEASURED",
  area_m2: 10,
  perimeter_m: 12,
  length_m: null,
  layer: null,
  source_id: null,
  ...over,
});

describe("measurementsCsv", () => {
  it("writes a header and one row per feature, quoting where needed", () => {
    const fc: MapCollection = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: null, properties: props({ name: 'Plot "A", north' }) },
        { type: "Feature", geometry: null, properties: props({ index: 1, name: "=HYPERLINK()", area_m2: null }) },
      ],
    };
    const lines = measurementsCsv(fc).trim().split("\r\n");
    expect(lines[0]).toBe("index,name,layer,source_id,geometry_type,status,area_m2,perimeter_m,length_m");
    expect(lines[1]).toBe('0,"Plot ""A"", north",,,Polygon,MEASURED,10,12,');
    // Formula injection is neutralised.
    expect(lines[2]).toBe("1,'=HYPERLINK(),,,Polygon,MEASURED,,12,");
  });
});

describe("geometryBounds", () => {
  it("handles nested coordinates and collections", () => {
    expect(
      geometryBounds({ type: "Polygon", coordinates: [[[0, 0], [2, 0], [2, 3], [0, 0]]] }),
    ).toEqual([0, 0, 2, 3]);
    expect(
      geometryBounds({
        type: "GeometryCollection",
        geometries: [
          { type: "Point", coordinates: [-1, 5] },
          { type: "LineString", coordinates: [[3, 1], [4, 2]] },
        ],
      }),
    ).toEqual([-1, 1, 4, 5]);
    expect(geometryBounds(null)).toBeNull();
  });
});

it("strips geo extensions from file names", () => {
  expect(baseName("survey.KML")).toBe("survey");
  expect(baseName("parcels.zip")).toBe("parcels");
});
