import { useMemo, useState } from "react";
import type { BaseMethod, BaseSpec, BatchVolumes, ProfileResult, Raster, VolumeResult } from "../api";
import { area, fmt, formatBytes, formatNumber, length, timeAgo, type UnitSystem } from "../units";
import type { DemLayer, LngLat } from "./MapView";

// ------------------------------------------------------------------ helpers

const BASES: { method: BaseMethod; label: string; hint: string }[] = [
  { method: "triangulated", label: "TIN", hint: "Triangulated through the boundary: follows irregular ground (default)" },
  { method: "plane", label: "Plane", hint: "Best-fit plane through the boundary: sloped, even ground" },
  { method: "lowest", label: "Lowest", hint: "Flat at the lowest boundary point: overestimates on slopes" },
  { method: "mean", label: "Mean", hint: "Flat at the mean boundary elevation" },
  { method: "custom", label: "Level", hint: "Flat at an elevation you choose (design level)" },
  { method: "surface", label: "Survey", hint: "Against an earlier survey: change between two dates" },
];

/** m³ with sensible precision. */
export function volume(m3: number, system: UnitSystem): string {
  if (system === "imperial") {
    const yd3 = m3 * 1.307950619;
    return `${formatNumber(yd3)} yd³`;
  }
  return `${formatNumber(m3)} m³`;
}

const signed = (m3: number, system: UnitSystem) => `${m3 > 0 ? "+" : m3 < 0 ? "−" : ""}${volume(Math.abs(m3), system)}`;

// ---------------------------------------------------------------- DEM list

interface DemListProps {
  dems: Raster[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}

export function DemList({ dems, activeId, onOpen, onDelete }: DemListProps) {
  const [confirming, setConfirming] = useState<string | null>(null);
  if (!dems.length) {
    return <p className="empty-note">No elevation models yet. Upload a DEM/DSM GeoTIFF or open the quarry sample.</p>;
  }
  return (
    <ul className="file-list dem-list">
      {dems.map((d) => (
        <li key={d.id} style={{ position: "relative" }}>
          <button
            type="button"
            className={`file-item${d.id === activeId ? " is-active" : ""}`}
            onClick={() => onOpen(d.id)}
            aria-current={d.id === activeId}
          >
            <span className="fmt fmt-dem">DEM</span>
            <span className="fname" title={d.filename}>
              {d.filename}
            </span>
            <span className="fmeta">
              <span className={`dot ${d.status}`} />{" "}
              {d.status === "COMPLETED"
                ? `${d.cell_size_m?.[0]?.toFixed(2)} m · ${d.z_min?.toFixed(0)}–${d.z_max?.toFixed(0)} m · ${formatBytes(d.size_bytes)}`
                : d.status.toLowerCase()}{" "}
              · {timeAgo(d.created_at)}
            </span>
          </button>
          <div
            className={`factions${confirming === d.id ? " is-confirming" : ""}`}
            style={{ position: "absolute", right: 14, top: "50%", transform: "translateY(-50%)" }}
          >
            {confirming === d.id ? (
              <span className="confirm">
                <button type="button" className="btn btn-sm btn-flag" onClick={() => onDelete(d.id)}>
                  Delete
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setConfirming(null)}>
                  Keep
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="icon-btn"
                aria-label={`Delete ${d.filename}`}
                title="Delete"
                onClick={() => setConfirming(d.id)}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
                </svg>
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------- terrain bar

interface TerrainBarProps {
  dem: Raster;
  layer: DemLayer;
  onLayer: (layer: DemLayer) => void;
  tool: "volume" | "profile" | null;
  onTool: (tool: "volume" | "profile" | null) => void;
  canReport: boolean;
  onReport: () => void;
  onClose: () => void;
  cursorElevation: number | null;
  top: number;
}

export function TerrainBar({ dem, layer, onLayer, tool, onTool, canReport, onReport, onClose, cursorElevation, top }: TerrainBarProps) {
  return (
    <section className="overlay terrain-bar" style={{ top }} aria-label="Elevation model tools">
      <div className="terrain-head">
        <span className="fmt fmt-dem">DEM</span>
        <div className="terrain-title">
          <strong title={dem.filename}>{dem.filename}</strong>
          <span className="mono">
            {dem.crs} · {dem.cell_size_m?.[0]?.toFixed(2)} m cells
          </span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close elevation model" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="segmented" role="group" aria-label="Elevation style">
        {(["elevation", "hillshade", "slope"] as const).map((s) => (
          <button key={s} type="button" aria-pressed={layer.style === s} onClick={() => onLayer({ ...layer, style: s })}>
            {s === "elevation" ? "Elevation" : s === "hillshade" ? "Hillshade" : "Slope"}
          </button>
        ))}
      </div>

      <Legend style={layer.style} zMin={dem.z_min ?? 0} zMax={dem.z_max ?? 1} />

      <label className="opacity">
        <span className="cell-label">Opacity</span>
        <input
          type="range"
          min={0.2}
          max={1}
          step={0.05}
          value={layer.opacity}
          onChange={(e) => onLayer({ ...layer, opacity: Number(e.target.value) })}
        />
      </label>

      <div className="terrain-tools">
        <button
          type="button"
          className={`btn btn-sm${tool === "volume" ? " btn-primary" : ""}`}
          onClick={() => onTool(tool === "volume" ? null : "volume")}
          title="Draw a polygon: cut/fill volume, 3D area, slope"
        >
          Volume
        </button>
        <button
          type="button"
          className={`btn btn-sm${tool === "profile" ? " btn-primary" : ""}`}
          onClick={() => onTool(tool === "profile" ? null : "profile")}
          title="Draw a line: elevation profile and grades"
        >
          Profile
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={!canReport}
          onClick={onReport}
          title={canReport ? "Volumes for every polygon in the open file" : "Open a file with polygons (e.g. stockpile outlines)"}
        >
          Stockpile report
        </button>
      </div>

      <div className="cursor-elev mono" aria-live="off">
        {cursorElevation != null ? `cursor ${cursorElevation.toFixed(2)} m` : "hover the map for elevation"}
      </div>
    </section>
  );
}

function Legend({ style, zMin, zMax }: { style: DemLayer["style"]; zMin: number; zMax: number }) {
  if (style === "slope") {
    const classes = [
      ["#7eb065", "<10°"],
      ["#e9d363", "10–20°"],
      ["#ee923d", "20–30°"],
      ["#d6402e", "30–45°"],
      ["#7a1620", ">45°"],
    ];
    return (
      <div className="legend-slope">
        {classes.map(([c, l]) => (
          <span key={l}>
            <i style={{ background: c }} />
            {l}
          </span>
        ))}
      </div>
    );
  }
  if (style === "hillshade") return <div className="legend-note">Sun from the north-west, 45° altitude</div>;
  return (
    <div className="legend-ramp">
      <span className="ramp" />
      <span className="mono">
        <span>{zMin.toFixed(1)} m</span>
        <span>{zMax.toFixed(1)} m</span>
      </span>
    </div>
  );
}

// ------------------------------------------------------------ base picker

interface BasePickerProps {
  base: BaseSpec;
  onBase: (b: BaseSpec) => void;
  surveys: Raster[];
}

function BasePicker({ base, onBase, surveys }: BasePickerProps) {
  const current = BASES.find((b) => b.method === base.method);
  return (
    <div className="base-picker">
      <div className="segmented wrap" role="group" aria-label="Base surface">
        {BASES.map((b) => (
          <button
            key={b.method}
            type="button"
            aria-pressed={base.method === b.method}
            title={b.hint}
            disabled={b.method === "surface" && !surveys.length}
            onClick={() =>
              onBase({
                method: b.method,
                elevation: b.method === "custom" ? (base.elevation ?? null) : null,
                raster_id: b.method === "surface" ? (base.raster_id ?? surveys[0]?.id ?? null) : null,
              })
            }
          >
            {b.label}
          </button>
        ))}
      </div>
      <p className="base-hint">{current?.hint}</p>
      {base.method === "custom" && (
        <label className="inline-field">
          Base elevation (m)
          <input
            type="number"
            step="0.1"
            value={base.elevation ?? ""}
            onChange={(e) => onBase({ ...base, elevation: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </label>
      )}
      {base.method === "surface" && (
        <label className="inline-field">
          Earlier survey
          <select value={base.raster_id ?? ""} onChange={(e) => onBase({ ...base, raster_id: e.target.value })}>
            {surveys.map((s) => (
              <option key={s.id} value={s.id}>
                {s.filename}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}

// ------------------------------------------------------------ volume panel

interface VolumePanelProps {
  title: string;
  result: VolumeResult | null;
  loading: boolean;
  error: string | null;
  base: BaseSpec;
  onBase: (b: BaseSpec) => void;
  surveys: Raster[];
  units: UnitSystem;
  onClose: () => void;
}

export function VolumePanel({ title, result, loading, error, base, onBase, surveys, units, onClose }: VolumePanelProps) {
  const r = result;
  const isCompare = base.method === "surface";
  return (
    <aside className="overlay inspector volume-panel" aria-label="Volume analysis">
      <header className="inspector-head">
        <div className="titles">
          <div className="kicker">Volume on DEM</div>
          <h3>{title}</h3>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close volume panel" title="Close (Esc)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <div className="inspector-body">
        <h4>Base surface</h4>
        <BasePicker base={base} onBase={onBase} surveys={surveys} />
        {error && <div className="alert">{error}</div>}
        {loading && <p className="empty-note">Computing on the DEM…</p>}
        {r && !loading && (
          <>
            <div className="big-measures">
              <div className="big-measure">
                <div className="label">{isCompare ? "Added since survey" : "Cut · material above base"}</div>
                <div className="value">{volume(r.cut_m3, units)}</div>
                <div className="exact">{r.cut_m3.toLocaleString("en-US", { maximumFractionDigits: 3 })} m³</div>
              </div>
              <div className="big-measure">
                <div className="label">{isCompare ? "Removed since survey" : "Fill · void below base"}</div>
                <div className="value">{volume(r.fill_m3, units)}</div>
                <div className="exact">{r.fill_m3.toLocaleString("en-US", { maximumFractionDigits: 3 })} m³</div>
              </div>
              <div className="big-measure net">
                <div className="label">Net (cut − fill)</div>
                <div className="value">{signed(r.net_m3, units)}</div>
              </div>
            </div>
            {r.warnings.map((w) => (
              <div className="note" key={w}>
                {w}
              </div>
            ))}
            <h4>Surface</h4>
            <table className="kv">
              <tbody>
                <tr><th>2D (planimetric) area</th><td>{fmt(area(r.area_2d_m2, units))}</td></tr>
                <tr><th>3D surface area</th><td>{fmt(area(r.surface_area_3d_m2, units))} <span className="muted">(+{((r.surface_area_3d_m2 / r.area_2d_m2 - 1) * 100).toFixed(1)}%)</span></td></tr>
                <tr><th>Max height above base</th><td>{fmt(length(r.max_height_above_base_m, units, units === "metric" ? "m" : "ft"))}</td></tr>
                <tr><th>Max depth below base</th><td>{fmt(length(r.max_depth_below_base_m, units, units === "metric" ? "m" : "ft"))}</td></tr>
                <tr><th>Elevation (min / mean / max)</th><td className="mono">{r.elevation.min.toFixed(2)} / {r.elevation.mean.toFixed(2)} / {r.elevation.max.toFixed(2)} m</td></tr>
              </tbody>
            </table>
            <h4>Slope</h4>
            <table className="kv">
              <tbody>
                <tr><th>Mean / max slope</th><td>{r.slope.mean_deg.toFixed(1)}° / {r.slope.max_deg.toFixed(1)}°</td></tr>
                <tr><th>Steeper than {r.slope.threshold_deg}°</th><td>{r.slope.steeper_than_threshold_pct.toFixed(1)}% of area</td></tr>
              </tbody>
            </table>
            <h4>Method</h4>
            <table className="kv">
              <tbody>
                <tr><th>Base</th><td>{r.base_method}{r.base_elevation.boundary_relief_m != null ? ` · boundary relief ${r.base_elevation.boundary_relief_m.toFixed(2)} m` : ""}</td></tr>
                {r.plane && <tr><th>Plane gradient</th><td className="mono">{(r.plane.dz_dx * 100).toFixed(2)}% E · {(r.plane.dz_dy * 100).toFixed(2)}% N</td></tr>}
                <tr><th>Cells</th><td className="mono">{r.cells.valid.toLocaleString()} valid · {r.cells.nodata.toLocaleString()} nodata ({r.coverage_pct}% coverage)</td></tr>
                <tr><th>Cell size</th><td className="mono">{r.cell_size_m[0]} × {r.cell_size_m[1]} m</td></tr>
                <tr><th>Cell geometry</th><td>{r.cell_method}</td></tr>
              </tbody>
            </table>
          </>
        )}
      </div>
    </aside>
  );
}

// ----------------------------------------------------------- profile panel

interface ProfilePanelProps {
  result: ProfileResult | null;
  loading: boolean;
  error: string | null;
  title: string;
  units: UnitSystem;
  height: number;
  onHoverPoint: (p: LngLat | null) => void;
  onClose: () => void;
}

export function ProfilePanel({ result, loading, error, title, units, height, onHoverPoint, onClose }: ProfilePanelProps) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000;
  const H = 200;
  const pad = { l: 56, r: 16, t: 14, b: 26 };

  const chart = useMemo(() => {
    if (!result) return null;
    const d = result.distance_m;
    const z = result.elevation_m;
    const valid = z.filter((v): v is number => v != null);
    if (!valid.length) return null;
    let zMin = Math.min(...valid);
    let zMax = Math.max(...valid);
    const span = Math.max(zMax - zMin, 1);
    zMin -= span * 0.1;
    zMax += span * 0.1;
    const xMax = d[d.length - 1] || 1;
    const sx = (v: number) => pad.l + (v / xMax) * (W - pad.l - pad.r);
    const sy = (v: number) => pad.t + (1 - (v - zMin) / (zMax - zMin)) * (H - pad.t - pad.b);
    let line = "";
    let pen = false;
    d.forEach((dist, i) => {
      const v = z[i];
      if (v == null) {
        pen = false;
        return;
      }
      line += `${pen ? "L" : "M"}${sx(dist).toFixed(1)},${sy(v).toFixed(1)}`;
      pen = true;
    });
    const area = `${line}L${sx(xMax).toFixed(1)},${H - pad.b}L${sx(0).toFixed(1)},${H - pad.b}Z`;
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => zMin + t * (zMax - zMin));
    const xTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * xMax);
    return { sx, sy, line, area, yTicks, xTicks, xMax };
  }, [result]);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!result || !chart) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const dist = ((x - pad.l) / (W - pad.l - pad.r)) * chart.xMax;
    let best = 0;
    for (let i = 1; i < result.distance_m.length; i++) {
      if (Math.abs(result.distance_m[i]! - dist) < Math.abs(result.distance_m[best]! - dist)) best = i;
    }
    setHover(best);
    onHoverPoint([result.lon[best]!, result.lat[best]!]);
  };

  const s = result?.stats;
  return (
    <section className="overlay ledger profile-panel" style={{ height }} aria-label="Elevation profile">
      <div className="ledger-head">
        <span className="title">Profile</span>
        <span className="count">{title}</span>
        {s && (
          <div className="profile-stats">
            <span><b>{fmt(length(result!.length_m, units))}</b> length</span>
            <span><b>+{s.total_climb_m.toFixed(1)} m</b> climb</span>
            <span><b>−{s.total_descent_m.toFixed(1)} m</b> descent</span>
            <span><b>{s.mean_abs_grade_pct.toFixed(1)}%</b> mean grade</span>
            <span className={s.max_sustained_grade_pct > 10 ? "warn" : undefined}>
              <b>{s.max_sustained_grade_pct.toFixed(1)}%</b> max sustained
            </span>
          </div>
        )}
        <span className="spacer" />
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close profile" title="Close">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="profile-body">
        {error && <div className="alert">{error}</div>}
        {loading && <p className="empty-note" style={{ padding: 14 }}>Sampling the DEM…</p>}
        {chart && result && !loading && (
          <svg
            className="profile-chart"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            onMouseMove={onMove}
            onMouseLeave={() => {
              setHover(null);
              onHoverPoint(null);
            }}
            role="img"
            aria-label={`Elevation profile, ${result.length_m.toFixed(0)} metres`}
          >
            {chart.yTicks.map((t) => (
              <g key={`y${t}`}>
                <line x1={pad.l} x2={W - pad.r} y1={chart.sy(t)} y2={chart.sy(t)} className="grid" />
                <text x={pad.l - 6} y={chart.sy(t) + 4} textAnchor="end" className="tick">
                  {t.toFixed(1)}
                </text>
              </g>
            ))}
            {chart.xTicks.map((t) => (
              <text key={`x${t}`} x={chart.sx(t)} y={H - 8} textAnchor="middle" className="tick">
                {t >= 1000 ? `${(t / 1000).toFixed(2)} km` : `${t.toFixed(0)} m`}
              </text>
            ))}
            <path d={chart.area} className="area" />
            <path d={chart.line} className="line" />
            {hover != null && result.elevation_m[hover] != null && (
              <g>
                <line
                  x1={chart.sx(result.distance_m[hover]!)}
                  x2={chart.sx(result.distance_m[hover]!)}
                  y1={pad.t}
                  y2={H - pad.b}
                  className="cursor"
                />
                <circle cx={chart.sx(result.distance_m[hover]!)} cy={chart.sy(result.elevation_m[hover]!)} r={4} className="dot-h" />
                <text x={chart.sx(result.distance_m[hover]!) + 8} y={pad.t + 12} className="tip">
                  {result.distance_m[hover]!.toFixed(1)} m · {result.elevation_m[hover]!.toFixed(2)} m
                </text>
              </g>
            )}
          </svg>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------- stockpile report

interface ReportProps {
  report: BatchVolumes | null;
  loading: boolean;
  error: string | null;
  base: BaseSpec;
  onBase: (b: BaseSpec) => void;
  surveys: Raster[];
  units: UnitSystem;
  height: number;
  demName: string;
  fileName: string;
  onPick: (index: number) => void;
  onExport: () => void;
  onClose: () => void;
}

export function StockpileReport(p: ReportProps) {
  const { report, loading, error, units, height } = p;
  const compare = p.base.method === "surface";
  return (
    <section className="overlay ledger report-panel" style={{ height }} aria-label="Stockpile report">
      <div className="ledger-head">
        <span className="title">Stockpile report</span>
        <span className="count">
          {p.fileName} on {p.demName}
        </span>
        <span className="spacer" />
        <button type="button" className="btn btn-sm" onClick={p.onExport} disabled={!report}>
          CSV
        </button>
        <button type="button" className="icon-btn" onClick={p.onClose} aria-label="Close report" title="Close">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="report-body">
        <div className="report-side">
          <BasePicker base={p.base} onBase={p.onBase} surveys={p.surveys} />
          {report && (
            <div className="report-totals">
              <div>
                <span className="cell-label">{compare ? "Total added" : "Total cut"}</span>
                <b>{volume(report.totals.cut_m3, units)}</b>
              </div>
              <div>
                <span className="cell-label">{compare ? "Total removed" : "Total fill"}</span>
                <b>{volume(report.totals.fill_m3, units)}</b>
              </div>
              <div>
                <span className="cell-label">Net</span>
                <b>{signed(report.totals.net_m3, units)}</b>
              </div>
              <span className="mono muted">
                {report.measured} measured · {report.skipped} skipped · {report.failed} failed
              </span>
            </div>
          )}
        </div>
        <div className="table-scroll">
          {error && <div className="alert">{error}</div>}
          {loading && <p className="empty-note" style={{ padding: 14 }}>Measuring every polygon on the DEM…</p>}
          {report && !loading && (
            <table className="report-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th className="num">{compare ? "Added" : "Cut"}</th>
                  <th className="num">{compare ? "Removed" : "Fill"}</th>
                  <th className="num">Net</th>
                  <th className="num">2D area</th>
                  <th className="num">3D area</th>
                  <th className="num">Max height</th>
                  <th className="num">Max slope</th>
                </tr>
              </thead>
              <tbody>
                {report.items.map((it) => (
                  <tr key={it.index} onClick={() => p.onPick(it.index)} className={it.status !== "OK" ? "muted" : undefined}>
                    <td className="num">{it.index}</td>
                    <td>{it.name ?? `Feature ${it.index}`}</td>
                    {it.result ? (
                      <>
                        <td className="num">{volume(it.result.cut_m3, units)}</td>
                        <td className="num">{volume(it.result.fill_m3, units)}</td>
                        <td className="num">{signed(it.result.net_m3, units)}</td>
                        <td className="num">{fmt(area(it.result.area_2d_m2, units))}</td>
                        <td className="num">{fmt(area(it.result.surface_area_3d_m2, units))}</td>
                        <td className="num">{it.result.max_height_above_base_m.toFixed(2)} m</td>
                        <td className="num">{it.result.slope.max_deg.toFixed(1)}°</td>
                      </>
                    ) : (
                      <td colSpan={7} className="muted">
                        {it.status.toLowerCase()}: {it.message}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
