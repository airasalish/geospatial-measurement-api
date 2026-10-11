import { describe, expect, it } from "vitest";
import { area, autoArea, fmt, formatLatLon, length, parseProj, timeAgo } from "./units";

describe("area", () => {
  it("picks a readable metric unit", () => {
    expect(autoArea(500, "metric")).toBe("m2");
    expect(autoArea(32_408, "metric")).toBe("ha");
    expect(autoArea(2_090_000, "metric")).toBe("km2");
  });

  it("converts and formats", () => {
    expect(fmt(area(32_408.162, "metric"))).toBe("3.24 ha");
    expect(fmt(area(4_046.8564224, "imperial"))).toBe("1.00 ac");
    expect(fmt(area(4_947.705, "metric", "m2"))).toBe("4,947.7 m²");
  });

  it("handles missing values", () => {
    expect(area(null, "metric")).toBeNull();
    expect(fmt(null)).toBe("—");
  });
});

describe("length", () => {
  it("switches to km / miles for long values", () => {
    expect(fmt(length(351.651, "metric"))).toBe("351.7 m");
    expect(fmt(length(4_680, "metric"))).toBe("4.68 km");
    expect(fmt(length(1_609.344, "imperial"))).toBe("1.00 mi");
    expect(fmt(length(100, "imperial"))).toBe("328.1 ft");
  });
});

describe("parseProj", () => {
  it("extracts projection and centre", () => {
    expect(parseProj("+proj=laea +lat_0=13.0 +lon_0=77.6 +datum=WGS84 +units=m +no_defs")).toEqual({
      proj: "laea",
      lat0: 13,
      lon0: 77.6,
    });
  });

  it("returns null for strings it can't read", () => {
    expect(parseProj("EPSG:4326")).toBeNull();
  });

  it("formats hemispheres", () => {
    expect(formatLatLon(-33.05, -70.04)).toBe("33.0°S 70.0°W");
  });
});

describe("timeAgo", () => {
  const now = Date.parse("2026-10-11T12:00:00Z");
  it("is relative for recent times", () => {
    expect(timeAgo("2026-10-11T11:59:50Z", now)).toBe("just now");
    expect(timeAgo("2026-10-11T11:55:00Z", now)).toBe("5 min ago");
    expect(timeAgo("2026-10-11T09:00:00Z", now)).toBe("3 h ago");
  });
});
