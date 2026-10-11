import { useEffect, useState } from "react";
import { api, type FeatureDetail, type Measurement } from "../api";
import { AREA_UNITS, area, formatLatLon, LENGTH_UNITS, length, parseProj, type Quantity, type UnitSystem } from "../units";
import { label } from "./MapView";

interface Props {
  fileId: string;
  index: number;
  /** Display name already derived by the API (name, else an id-like attribute). */
  name: string | null;
  units: UnitSystem;
  onClose: () => void;
  onZoom: () => void;
}

interface Loaded {
  feature: FeatureDetail;
  measurement: Measurement | null;
}

const PROJ_NAMES: Record<string, string> = {
  laea: "Lambert Azimuthal Equal-Area",
  aeqd: "Azimuthal Equidistant",
};

export function Inspector({ fileId, index, name, units, onClose, onZoom }: Props) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setData(null);
    setError(null);
    Promise.all([api.feature(fileId, index, ctrl.signal), api.measurement(fileId, index, ctrl.signal)])
      .then(([feature, m]) => setData({ feature, measurement: m.items[0] ?? null }))
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });
    return () => ctrl.abort();
  }, [fileId, index]);

  const m = data?.measurement;

  const copyGeometry = async () => {
    if (!data?.feature.geometry) return;
    await navigator.clipboard.writeText(JSON.stringify(data.feature.geometry));
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <aside className="overlay inspector" aria-label={`Feature ${index}`}>
      <header className="inspector-head">
        <div className="titles">
          <div className="kicker">
            Feature #{index}
            {data?.feature.layer ? ` · ${data.feature.layer}` : ""}
          </div>
          <h3>{name || data?.feature.geometry_type || "Feature"}</h3>
          {m && (
            <span className="pill">
              <span className={`dot ${m.status}`} />
              {label(m.status)} · {m.geometry_type ?? "no geometry"}
            </span>
          )}
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close inspector" title="Close (Esc)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="inspector-body">
        {error && <div className="alert">{error}</div>}
        {!data && !error && <p className="empty-note">Loading feature…</p>}
        {m && (
          <>
            {m.status === "MEASURED" && (
              <div className="big-measures">
                {m.area_m2 != null && <Big title="Area" q={area(m.area_m2, units)} exact={exactArea(m.area_m2)} />}
                {m.perimeter_m != null && (
                  <Big title="Perimeter" q={length(m.perimeter_m, units)} exact={exactLength(m.perimeter_m)} />
                )}
                {m.length_m != null && <Big title="Length" q={length(m.length_m, units)} exact={exactLength(m.length_m)} />}
              </div>
            )}
            {m.note && <div className="note">{m.note}</div>}
            {Object.keys(m.measurement_crs).length > 0 && (
              <>
                <h4>Method</h4>
                <Method crs={m.measurement_crs} />
              </>
            )}
          </>
        )}
        {data && (
          <>
            <h4>Source</h4>
            <table className="kv">
              <tbody>
                <Row k="Source CRS" v={data.feature.crs} />
                <Row k="Geometry" v={data.feature.geometry_type} />
                <Row k="Source ID" v={data.feature.source_id} />
                <Row k="Layer" v={data.feature.layer} />
              </tbody>
            </table>
            <h4>Properties ({Object.keys(data.feature.properties).length})</h4>
            {Object.keys(data.feature.properties).length ? (
              <table className="kv">
                <tbody>
                  {Object.entries(data.feature.properties).map(([k, v]) => (
                    <Row key={k} k={k} v={v} />
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="empty-note">No attributes.</p>
            )}
          </>
        )}
      </div>

      <footer className="inspector-actions">
        <button type="button" className="btn btn-sm btn-primary" onClick={onZoom}>
          Zoom to feature
        </button>
        <button type="button" className="btn btn-sm" onClick={copyGeometry} disabled={!data?.feature.geometry}>
          {copied ? "Copied ✓" : "Copy GeoJSON"}
        </button>
      </footer>
    </aside>
  );
}

function Big({ title, q, exact }: { title: string; q: Quantity | null; exact: string }) {
  return (
    <div className="big-measure">
      <div className="label">{title}</div>
      <div className="value">
        {q?.value ?? "—"}
        <small>{q?.unit}</small>
      </div>
      <div className="exact">{exact}</div>
    </div>
  );
}

const exactArea = (m2: number) =>
  `${m2.toLocaleString("en-US", { maximumFractionDigits: 3 })} m² · ${(m2 * AREA_UNITS.ac.perM2).toLocaleString("en-US", { maximumFractionDigits: 4 })} ac`;
const exactLength = (m: number) =>
  `${m.toLocaleString("en-US", { maximumFractionDigits: 3 })} m · ${(m * LENGTH_UNITS.ft.perM).toLocaleString("en-US", { maximumFractionDigits: 2 })} ft`;

function Method({ crs }: { crs: Record<string, string> }) {
  const entries = Object.entries(crs);
  return (
    <div className="method">
      {entries.map(([what, def]) => {
        const p = parseProj(def);
        return (
          <p key={what}>
            <b style={{ textTransform: "capitalize" }}>{what}</b> measured in{" "}
            {p ? (
              <>
                {PROJ_NAMES[p.proj] ?? p.proj} centred on <span className="mono">{formatLatLon(p.lat0, p.lon0)}</span>
              </>
            ) : (
              def
            )}
          </p>
        );
      })}
      <p style={{ color: "var(--text-3)", fontSize: 12 }}>
        Long edges are densified along WGS84 geodesics first. Reproduce with the PROJ string:
      </p>
      <code>{entries[0]?.[1]}</code>
    </div>
  );
}

function Row({ k, v }: { k: string; v: unknown }) {
  const empty = v == null || v === "";
  return (
    <tr>
      <th>{k}</th>
      <td className={empty ? "null" : undefined}>{empty ? "null" : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
    </tr>
  );
}
