import type { GeoFile, MeasurementStatus, MeasurementSummary } from "../api";
import { area, length, type UnitSystem } from "../units";
import { label } from "./MapView";

const STATUS_ORDER: MeasurementStatus[] = ["MEASURED", "NOT_APPLICABLE", "UNSUPPORTED", "FAILED"];
const STATUS_COLOR: Record<MeasurementStatus, string> = {
  MEASURED: "var(--ok)",
  NOT_APPLICABLE: "var(--text-3)",
  UNSUPPORTED: "var(--warn)",
  FAILED: "var(--err)",
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
          <Value q={summary && summary.total_area_m2 > 0 ? area(summary.total_area_m2, units) : null} />
        </div>
        <div className="cell">
          <span className="cell-label">Total length</span>
          <Value q={summary && summary.total_length_m > 0 ? length(summary.total_length_m, units) : null} />
        </div>
        <div className="cell">
          <span className="cell-label">Perimeter</span>
          <Value q={summary && summary.total_perimeter_m > 0 ? length(summary.total_perimeter_m, units) : null} />
        </div>
        <div className="cell">
          <span className="cell-label">Features</span>
          <span className="cell-value">{total.toLocaleString()}</span>
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
}

export function MapControls({ basemap, onBasemap, showLabels, onLabels }: ControlsProps) {
  return (
    <div className="overlay map-controls">
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
          <i style={{ background: "rgba(198,244,50,.2)", border: "1.5px solid #c6f432" }} />
          Area
        </span>
        <span>
          <i style={{ height: 3, background: "#3cc4ee" }} />
          Length
        </span>
        <span>
          <i style={{ width: 9, height: 9, borderRadius: "50%", background: "#e9edf2" }} />
          Point
        </span>
        <span>
          <i style={{ background: "#ff5d5d" }} />
          Failed
        </span>
      </div>
    </div>
  );
}
