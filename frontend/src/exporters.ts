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
