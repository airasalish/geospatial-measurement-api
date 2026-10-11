import { describe, expect, it } from "vitest";
import { sketchKml } from "./exporters";
import { sphericalArea, sphericalLength } from "./geo";

describe("spherical estimates", () => {
  it("measures a 0.01° square near Bengaluru within 0.5% of the ellipsoidal value", () => {
    const sq: [number, number][] = [[77.59, 12.97], [77.6, 12.97], [77.6, 12.98], [77.59, 12.98]];
    // Geodesic (WGS84) ground truth from the backend tests: 1,200,289.8 m².
    expect(sphericalArea(sq)).toBeGreaterThan(1_200_289.8 * 0.995);
    expect(sphericalArea(sq)).toBeLessThan(1_200_289.8 * 1.005);
    // Closed or open ring gives the same answer.
    expect(sphericalArea([...sq, sq[0]!])).toBeCloseTo(sphericalArea(sq), 6);
  });

  it("measures one degree of longitude at the equator", () => {
    expect(sphericalLength([[0, 0], [1, 0]])).toBeCloseTo(111_195, -1);
  });

  it("returns 0 for degenerate input", () => {
    expect(sphericalArea([[0, 0], [1, 1]])).toBe(0);
    expect(sphericalLength([[0, 0]])).toBe(0);
  });
});

describe("sketchKml", () => {
  it("closes polygons and escapes the name", () => {
    const kml = sketchKml([[1, 2], [3, 4], [5, 2]], "polygon", "A <b> & c");
    expect(kml).toContain("<Polygon>");
    expect(kml).toContain("1.00000000,2.00000000 3.00000000,4.00000000 5.00000000,2.00000000 1.00000000,2.00000000");
    expect(kml).toContain("<name>A b  c</name>");
  });

  it("writes lines as LineString", () => {
    expect(sketchKml([[1, 2], [3, 4]], "line", "road")).toContain("<LineString><coordinates>1.00000000,2.00000000 3.00000000,4.00000000");
  });
});
