// Unit conversion and number formatting. The API always returns SI (m, m²).

export type AreaUnit = "m2" | "ha" | "km2" | "ac";
export type LengthUnit = "m" | "km" | "ft" | "mi";
export type UnitSystem = "metric" | "imperial";

export const AREA_UNITS: Record<AreaUnit, { label: string; perM2: number }> = {
  m2: { label: "m²", perM2: 1 },
  ha: { label: "ha", perM2: 1e-4 },
  km2: { label: "km²", perM2: 1e-6 },
  ac: { label: "ac", perM2: 1 / 4046.8564224 },
};

export const LENGTH_UNITS: Record<LengthUnit, { label: string; perM: number }> = {
  m: { label: "m", perM: 1 },
  km: { label: "km", perM: 1e-3 },
  ft: { label: "ft", perM: 1 / 0.3048 },
  mi: { label: "mi", perM: 1 / 1609.344 },
};

/** Pick a readable unit for a value, e.g. 1.2 km² instead of 1,200,000 m². */
export function autoArea(m2: number, system: UnitSystem): AreaUnit {
  if (system === "imperial") return "ac";
  if (m2 >= 1e6) return "km2";
  if (m2 >= 1e4) return "ha";
  return "m2";
}

export function autoLength(m: number, system: UnitSystem): LengthUnit {
  if (system === "imperial") return m >= 1609.344 ? "mi" : "ft";
  return m >= 1000 ? "km" : "m";
}

/** Significant-figure aware: big numbers get fewer decimals, small ones more. */
export function formatNumber(value: number, maxDecimals = 3): string {
  const abs = Math.abs(value);
  const decimals = abs >= 1e5 ? 0 : abs >= 100 ? 1 : abs >= 1 ? 2 : maxDecimals;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: Math.min(decimals, 2),
    maximumFractionDigits: decimals,
  });
}

export interface Quantity {
  value: string;
  unit: string;
}

export function area(
  m2: number | null | undefined,
  system: UnitSystem,
  unit?: AreaUnit,
  unitFrom: number | null = m2 ?? null,
): Quantity | null {
  if (m2 == null) return null;
  const u = unit ?? autoArea(unitFrom ?? m2, system);
  return { value: formatNumber(m2 * AREA_UNITS[u].perM2), unit: AREA_UNITS[u].label };
}

export function length(
  m: number | null | undefined,
  system: UnitSystem,
  unit?: LengthUnit,
  unitFrom: number | null = m ?? null,
): Quantity | null {
  if (m == null) return null;
  const u = unit ?? autoLength(unitFrom ?? m, system);
  return { value: formatNumber(m * LENGTH_UNITS[u].perM), unit: LENGTH_UNITS[u].label };
}

export const fmt = (q: Quantity | null): string => (q ? `${q.value} ${q.unit}` : "—");

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function timeAgo(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** "+proj=laea +lat_0=13.0 +lon_0=77.6 ..." -> { proj: "laea", lat0: 13, lon0: 77.6 } */
export function parseProj(definition: string): { proj: string; lat0: number; lon0: number } | null {
  const get = (key: string) => definition.match(new RegExp(`\\+${key}=(\\S+)`))?.[1];
  const proj = get("proj");
  const lat0 = Number(get("lat_0"));
  const lon0 = Number(get("lon_0"));
  if (!proj || Number.isNaN(lat0) || Number.isNaN(lon0)) return null;
  return { proj, lat0, lon0 };
}

export function formatLatLon(lat: number, lon: number): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(1)}°${ns} ${Math.abs(lon).toFixed(1)}°${ew}`;
}
