import { useEffect, useRef, useState } from "react";
import type { GeoFile, MeasurementStatus, MeasurementSummary } from "../api";
import { sphericalArea, sphericalLength } from "../geo";
import { area, fmt, length, type UnitSystem } from "../units";
import { type DrawKind, label, type LngLat } from "./MapView";

/** Animate a number towards `target` (ease-out), skipping animation for reduced-motion users. */
function useCountUp(target: number, duration = 700): number {
  const [value, setValue] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const start = from.current;
    if (reduce || start === target) {
      from.current = target;
      setValue(target);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / duration);
      const v = start + (target - start) * (1 - (1 - t) ** 3);
      setValue(v);
      from.current = v;
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return value;
}

function CountArea({ m2, units }: { m2: number; units: UnitSystem }) {
  const v = useCountUp(m2);
  return <Value q={m2 > 0 ? area(v, units, undefined, m2) : null} />;
}

function CountLength({ m, units }: { m: number; units: UnitSystem }) {
  const v = useCountUp(m);
  return <Value q={m > 0 ? length(v, units, undefined, m) : null} />;
}

function CountInt({ n }: { n: number }) {
  const v = useCountUp(n);
  return <span className="cell-value">{Math.round(v).toLocaleString()}</span>;
}

const STATUS_ORDER: MeasurementStatus[] = ["MEASURED", "NOT_APPLICABLE", "UNSUPPORTED", "FAILED"];
const STATUS_COLOR: Record<MeasurementStatus, string> = {
  MEASURED: "var(--grass)",
  NOT_APPLICABLE: "var(--ink-3)",
  UNSUPPORTED: "var(--ochre)",
  FAILED: "var(--brick)",
};

function Value({ q }: { q: { value: string; unit: string } | null }) {
  if (!q) return <span className="cell-value">—</span>;
  return (
    <span className="cell-value">
      {q.value}
      <small>{q.unit}</small>
    </span>
  );
}

interface ReadoutProps {
  file: GeoFile;
  summary: MeasurementSummary | null;
  units: UnitSystem;
  onUnits: (u: UnitSystem) => void;
  onExportCsv: () => void;
  geojsonHref: string;
  truncated: boolean;
}

export function Readout({ file, summary, units, onUnits, onExportCsv, geojsonHref, truncated }: ReadoutProps) {
  const total = summary?.feature_count ?? file.feature_count;
  return (
    <section className="overlay readout" aria-label="File summary">
      <div className="readout-file">
        <span className="fname" title={file.filename}>
          {file.filename}
        </span>
        <span className="fmeta">
          {file.crs ?? "CRS unknown"} · {file.processing_ms != null ? `${Math.round(file.processing_ms)} ms` : "—"}
        </span>
      </div>
      <div className="readout-cells">
        <div className="cell">
          <span className="cell-label">Total area</span>
          <CountArea m2={summary?.total_area_m2 ?? 0} units={units} />
        </div>
        <div className="cell">
          <span className="cell-label">Total length</span>
          <CountLength m={summary?.total_length_m ?? 0} units={units} />
        </div>
        <div className="cell">
          <span className="cell-label">Perimeter</span>
          <CountLength m={summary?.total_perimeter_m ?? 0} units={units} />
        </div>
        <div className="cell">
          <span className="cell-label">Features</span>
          <CountInt n={total} />
        </div>
        <div className="cell cell-status">
          <span className="cell-label">Measurement status</span>
          <div className="status-bar" role="img" aria-label="Share of features by status">
            {STATUS_ORDER.map((s) => {
              const n = summary?.by_status[s] ?? 0;
              return n ? (
                <span key={s} style={{ width: `${(n / Math.max(total, 1)) * 100}%`, background: STATUS_COLOR[s] }} />
              ) : null;
            })}
          </div>
          <div className="status-legend">
            {STATUS_ORDER.filter((s) => summary?.by_status[s]).map((s) => (
              <span key={s}>
                <span className={`dot ${s}`} /> {summary!.by_status[s]} {label(s).toLowerCase()}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="readout-tools">
        <div className="row">
          <div className="segmented" role="group" aria-label="Units">
            <button type="button" aria-pressed={units === "metric"} onClick={() => onUnits("metric")}>
              Metric
            </button>
            <button type="button" aria-pressed={units === "imperial"} onClick={() => onUnits("imperial")}>
              Imperial
            </button>
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn btn-sm" onClick={onExportCsv} disabled={!total}>
            CSV
          </button>
          <a className="btn btn-sm" href={geojsonHref} download={`${file.filename}.geojson`} title={truncated ? "Capped at server limit" : undefined}>
            GeoJSON
          </a>
        </div>
      </div>
    </section>
  );
}

interface EmptyProps {
  onSample: (file: string) => void;
  onBrowse: () => void;
  busy: boolean;
}

export function EmptyState({ onSample, onBrowse, busy }: EmptyProps) {
  return (
    <section className="overlay empty" aria-label="Get started">
      <div className="eyebrow">Shapefile · KML · KMZ</div>
      <h2>
        Measure the ground, <em>not the map.</em>
      </h2>
      <p>
        Upload survey data and every polygon, line and point is measured on the WGS84 ellipsoid. Each feature is
        projected into its own equal-area or equidistant frame, so results hold anywhere on Earth, poles included.
      </p>
      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={onBrowse} disabled={busy}>
          Upload a file
        </button>
        <button type="button" className="btn" onClick={() => onSample("site_survey.kml")} disabled={busy}>
          Try: site survey (KML)
        </button>
        <button type="button" className="btn" onClick={() => onSample("farm_plots.zip")} disabled={busy}>
          Try: 400 farm plots (SHP)
        </button>
      </div>
      <p className="empty-hint">
        No survey files on this device? Download a sample (
        <a href="/samples/site_survey.kml" download>KML</a>,{" "}
        <a href="/samples/farm_plots.zip" download>Shapefile</a> or{" "}
        <a href="/samples/parcels_utm43n.zip" download>UTM Shapefile</a>) and drop it in.
      </p>
      <div className="spec">
        <div>
          <b>~10⁻⁹</b>
          <span>area error vs. geodesic ground truth</span>
        </div>
        <div>
          <b>5k+/s</b>
          <span>features measured, vectorised</span>
        </div>
        <div>
          <b>Any CRS</b>
          <span>UTM, Web Mercator, NAD83, no .prj…</span>
        </div>
      </div>
    </section>
  );
}

interface ControlsProps {
  basemap: "map" | "satellite";
  onBasemap: (b: "map" | "satellite") => void;
  showLabels: boolean;
  onLabels: (v: boolean) => void;
  draw: DrawKind | null;
  onDraw: (kind: DrawKind | null) => void;
  disabled: boolean;
}

export function MapControls({ basemap, onBasemap, showLabels, onLabels, draw, onDraw, disabled }: ControlsProps) {
  return (
    <div className="overlay map-controls">
      <div className="segmented draw-tools" role="group" aria-label="Measure on the map">
        <button
          type="button"
          aria-pressed={draw === "polygon"}
          disabled={disabled}
          onClick={() => onDraw(draw === "polygon" ? null : "polygon")}
          title="Draw an area on the map and measure it"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M4 18 7 5l11-2 3 11-9 7Z" strokeLinejoin="round" />
          </svg>
          Area
        </button>
        <button
          type="button"
          aria-pressed={draw === "line"}
          disabled={disabled}
          onClick={() => onDraw(draw === "line" ? null : "line")}
          title="Draw a line on the map and measure its length"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M3 19 10 9l5 5 6-10" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Distance
        </button>
      </div>
      <div className="segmented" role="group" aria-label="Basemap">
        <button type="button" aria-pressed={basemap === "map"} onClick={() => onBasemap("map")}>
          Map
        </button>
        <button type="button" aria-pressed={basemap === "satellite"} onClick={() => onBasemap("satellite")}>
          Satellite
        </button>
      </div>
      <label className="toggle">
        <input type="checkbox" checked={showLabels} onChange={(e) => onLabels(e.target.checked)} />
        Labels
      </label>
      <div className="legend" aria-label="Legend">
        <span>
          <i style={{ background: "rgba(228,83,42,.35)", border: "1.5px solid #c4441f" }} />
          Area
        </span>
        <span>
          <i style={{ height: 3, background: "#2b6d8e" }} />
          Length
        </span>
        <span>
          <i style={{ width: 9, height: 9, borderRadius: "50%", background: "#1c1f1d" }} />
          Point
        </span>
        <span>
          <i style={{ background: "#b4382a" }} />
          Failed
        </span>
      </div>
    </div>
  );
}

interface DrawHudProps {
  kind: DrawKind;
  vertices: LngLat[];
  cursor: LngLat | null;
  units: UnitSystem;
  onFinish: () => void;
  onCancel: () => void;
}

/** Live readout while sketching. A quick sphere estimate; the API returns the exact value. */
export function DrawHud({ kind, vertices, cursor, units, onFinish, onCancel }: DrawHudProps) {
  const pts = cursor ? [...vertices, cursor] : vertices;
  const need = kind === "polygon" ? 3 : 2;
  const ready = vertices.length >= need;
  const primary =
    kind === "polygon"
      ? pts.length >= 3
        ? fmt(area(sphericalArea(pts), units))
        : "—"
      : pts.length >= 2
        ? fmt(length(sphericalLength(pts), units))
        : "—";
  const secondary =
    kind === "polygon" && pts.length >= 3 ? `perimeter ≈ ${fmt(length(sphericalLength([...pts, pts[0]!]), units))}` : null;
  return (
    <section className="overlay draw-hud" aria-live="polite">
      <div className="draw-hud-main">
        <span className="cell-label">{kind === "polygon" ? "Measuring area" : "Measuring distance"}</span>
        <span className="draw-hud-value">≈ {primary}</span>
        <span className="draw-hud-sub">
          {vertices.length} point{vertices.length === 1 ? "" : "s"}
          {secondary ? ` · ${secondary}` : ""}
        </span>
      </div>
      <p className="draw-hud-hint">
        Click to add points · {kind === "polygon" ? "click the first point, " : ""}double-click or <kbd>Enter</kbd> to
        finish · <kbd>⌫</kbd> undo · <kbd>Esc</kbd> cancel
      </p>
      <div className="draw-hud-actions">
        <button type="button" className="btn btn-sm btn-primary" disabled={!ready} onClick={onFinish}>
          Measure precisely
        </button>
        <button type="button" className="btn btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}

export function DropVeil() {
  return (
    <div className="drop-veil" aria-hidden="true">
      <div className="drop-veil-card">
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 15V4m0 0L8 8m4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" strokeLinecap="round" />
        </svg>
        <strong>Drop to measure</strong>
        <span>Shapefile .zip · .kml · .kmz</span>
      </div>
    </div>
  );
}
