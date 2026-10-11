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

// ---- quick on-the-sphere estimates for the live drawing readout. The exact
// ellipsoidal value comes from the API once the sketch is submitted.

const R = 6_371_008.8; // mean Earth radius (m)
const rad = (d: number) => (d * Math.PI) / 180;

export type LonLat = [number, number];

/** Great-circle length of a path (haversine), in metres. */
export function sphericalLength(path: LonLat[]): number {
  let total = 0;
  for (let i = 1; i < path.length; i++) {
    const [lon1, lat1] = path[i - 1]!;
    const [lon2, lat2] = path[i]!;
    const dLat = rad(lat2 - lat1);
    const dLon = rad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
    total += 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  return total;
}

/** Area of a simple polygon on the sphere, in m² (ring may be open or closed). */
export function sphericalArea(ring: LonLat[]): number {
  const pts = ring.length > 1 && ring[0]![0] === ring.at(-1)![0] && ring[0]![1] === ring.at(-1)![1] ? ring.slice(0, -1) : ring;
  if (pts.length < 3) return 0;
  let sum = 0;
  for (let i = 0; i < pts.length; i++) {
    const [lon1, lat1] = pts[i]!;
    const [lon2, lat2] = pts[(i + 1) % pts.length]!;
    sum += rad(lon2 - lon1) * (2 + Math.sin(rad(lat1)) + Math.sin(rad(lat2)));
  }
  return Math.abs((sum * R * R) / 2);
}
