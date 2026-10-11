import type { MapCollection } from "./api";

const CSV_COLUMNS = [
  "index", "name", "layer", "source_id", "geometry_type", "status", "area_m2", "perimeter_m", "length_m",
] as const;

function csvCell(value: unknown): string {
  if (value == null) return "";
  const s = String(value);
  // Quote when needed; neutralise spreadsheet formula injection (=, +, -, @ at start).
  const safe = /^[=+\-@]/.test(s) && Number.isNaN(Number(s)) ? `'${s}` : s;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function measurementsCsv(collection: MapCollection): string {
  const lines = [CSV_COLUMNS.join(",")];
  for (const f of collection.features) {
    lines.push(CSV_COLUMNS.map((c) => csvCell(f.properties[c])).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

export function downloadText(content: string, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function baseName(filename: string): string {
  return filename.replace(/\.(zip|kml|kmz)$/i, "");
}

/** A one-feature KML document for a sketch drawn on the map. */
export function sketchKml(coords: [number, number][], kind: "polygon" | "line", name: string): string {
  const pts = kind === "polygon" ? [...coords, coords[0]!] : coords;
  const text = pts.map(([lon, lat]) => `${lon.toFixed(8)},${lat.toFixed(8)}`).join(" ");
  const geometry =
    kind === "polygon"
      ? `<Polygon><outerBoundaryIs><LinearRing><coordinates>${text}</coordinates></LinearRing></outerBoundaryIs></Polygon>`
      : `<LineString><coordinates>${text}</coordinates></LineString>`;
  const safe = name.replace(/[<&>]/g, "");
  return (
    `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>` +
    `<Placemark><name>${safe}</name>${geometry}</Placemark></Document></kml>\n`
  );
}

/** Stockpile report as CSV: one row per polygon, totals last. Volumes in m³. */
export function stockpileCsv(
  report: import("./api").BatchVolumes,
  meta: { dem: string; file: string; base: string },
): string {
  const head = [
    "index", "name", "status", "cut_m3", "fill_m3", "net_m3", "area_2d_m2", "surface_area_3d_m2",
    "max_height_m", "max_slope_deg", "base_method", "coverage_pct", "message",
  ];
  const lines = [
    `# Traverse stockpile report; DEM: ${meta.dem}; outlines: ${meta.file}; base: ${meta.base}`,
    head.join(","),
  ];
  for (const it of report.items) {
    const r = it.result;
    lines.push(
      [
        it.index, it.name, it.status, r?.cut_m3, r?.fill_m3, r?.net_m3, r?.area_2d_m2, r?.surface_area_3d_m2,
        r?.max_height_above_base_m, r?.slope.max_deg, r?.base_method, r?.coverage_pct, it.message,
      ].map(csvCell).join(","),
    );
  }
  const t = report.totals;
  lines.push(["", "TOTAL", "", t.cut_m3, t.fill_m3, t.net_m3, t.area_2d_m2, t.surface_area_3d_m2].map(csvCell).join(","));
  return lines.join("\r\n") + "\r\n";
}
