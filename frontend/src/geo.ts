export type BBox = [number, number, number, number];

/** Bounding box of any GeoJSON geometry (null for empty/missing). */
export function geometryBounds(geometry: GeoJSON.Geometry | null | undefined): BBox | null {
  if (!geometry) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const visit = (coords: unknown): void => {
    if (!Array.isArray(coords)) return;
    if (typeof coords[0] === "number") {
      const [x, y] = coords as number[];
      if (x! < minX) minX = x!;
      if (x! > maxX) maxX = x!;
      if (y! < minY) minY = y!;
      if (y! > maxY) maxY = y!;
      return;
    }
    for (const c of coords) visit(c);
  };
  if (geometry.type === "GeometryCollection") {
    for (const g of geometry.geometries) {
      const b = geometryBounds(g);
      if (b) visit([[b[0], b[1]], [b[2], b[3]]]);
    }
  } else {
    visit(geometry.coordinates);
  }
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

export const isPolygonal = (t: string | null | undefined) => t === "Polygon" || t === "MultiPolygon";
export const isLineal = (t: string | null | undefined) => t === "LineString" || t === "MultiLineString";
