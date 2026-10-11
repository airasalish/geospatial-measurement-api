import { useEffect, useMemo, useRef, useState } from "react";
import type { MapCollection, MapFeatureProps, MeasurementStatus } from "../api";
import { area, fmt, length, type UnitSystem } from "../units";
import { label } from "./MapView";

const ROW_H = 32;
const OVERSCAN = 8;

type SortKey = "index" | "name" | "layer" | "geometry_type" | "status" | "area_m2" | "perimeter_m" | "length_m";

const COLUMNS: { key: SortKey; title: string; numeric?: boolean }[] = [
  { key: "index", title: "#", numeric: true },
  { key: "name", title: "Name" },
  { key: "layer", title: "Layer" },
  { key: "geometry_type", title: "Type" },
  { key: "status", title: "Status" },
  { key: "area_m2", title: "Area", numeric: true },
  { key: "perimeter_m", title: "Perimeter", numeric: true },
  { key: "length_m", title: "Length", numeric: true },
];

const FILTERS: (MeasurementStatus | "ALL")[] = ["ALL", "MEASURED", "NOT_APPLICABLE", "UNSUPPORTED", "FAILED"];

interface Props {
  collection: MapCollection;
  units: UnitSystem;
  selected: number | null;
  hovered: number | null;
  onHover: (index: number | null) => void;
  onPick: (index: number) => void;
  open: boolean;
  onToggle: () => void;
  height: number;
}

export function Ledger({ collection, units, selected, hovered, onHover, onPick, open, onToggle, height }: Props) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<MeasurementStatus | "ALL">("ALL");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "index", dir: 1 });
  const scroller = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [scrollTop, setScrollTop] = useState(0);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: collection.features.length };
    for (const f of collection.features) c[f.properties.status] = (c[f.properties.status] ?? 0) + 1;
    return c;
  }, [collection]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list: MapFeatureProps[] = collection.features.map((f) => f.properties);
    if (status !== "ALL") list = list.filter((p) => p.status === status);
    if (q) {
      list = list.filter((p) =>
        [p.name, p.layer, p.source_id, p.geometry_type, String(p.index)].some((v) => v?.toLowerCase().includes(q)),
      );
    }
    const { key, dir } = sort;
    if (key !== "index" || dir !== 1) {
      list = [...list].sort((a, b) => {
        const av = a[key];
        const bv = b[key];
        if (av == null && bv == null) return 0;
        if (av == null) return 1; // nulls last regardless of direction
        if (bv == null) return -1;
        return (av < bv ? -1 : av > bv ? 1 : 0) * dir;
      });
    }
    return list;
  }, [collection, query, status, sort]);

  // "/" focuses search, like most data tools.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && open && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Keep the selected row visible when it is picked on the map.
  useEffect(() => {
    if (selected == null || !scroller.current) return;
    const pos = rows.findIndex((r) => r.index === selected);
    if (pos < 0) return;
    const el = scroller.current;
    const top = pos * ROW_H;
    const viewH = el.clientHeight - ROW_H; // minus sticky header
    if (top < el.scrollTop || top > el.scrollTop + viewH - ROW_H) el.scrollTo({ top: Math.max(0, top - viewH / 2) });
  }, [selected]);

  const viewH = height - 46 - ROW_H;
  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const end = Math.min(rows.length, Math.ceil((scrollTop + viewH) / ROW_H) + OVERSCAN);

  const toggleSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: key.endsWith("_m") || key.endsWith("m2") ? -1 : 1 }));

  if (!open) {
    return (
      <div className="overlay ledger ledger-collapsed">
        <div className="ledger-head" style={{ borderBottom: 0 }}>
          <span className="title">Ledger</span>
          <span className="count">{collection.features.length.toLocaleString()} features</span>
          <button type="button" className="btn btn-sm" onClick={onToggle} aria-expanded={false}>
            Open
          </button>
        </div>
      </div>
    );
  }

  return (
    <section className="overlay ledger" style={{ height }} aria-label="Feature ledger">
      <div className="ledger-head">
        <span className="title">Ledger</span>
        <span className="count">
          {rows.length.toLocaleString()}
          {rows.length !== collection.features.length && ` of ${collection.features.length.toLocaleString()}`}
        </span>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }} role="group" aria-label="Filter by status">
          {FILTERS.filter((f) => f === "ALL" || counts[f]).map((f) => (
            <button key={f} type="button" className="chip" aria-pressed={status === f} onClick={() => setStatus(f)}>
              {f !== "ALL" && <span className={`dot ${f}`} />}
              {f === "ALL" ? "All" : label(f)} <span style={{ opacity: 0.6 }}>{counts[f]}</span>
            </button>
          ))}
        </div>
        <span className="spacer" />
        <input
          ref={searchRef}
          className="search"
          type="search"
          placeholder="Search name, layer, id…  /"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search features"
        />
        <button type="button" className="icon-btn" onClick={onToggle} aria-expanded={true} aria-label="Collapse ledger" title="Collapse">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <div className="table-scroll" ref={scroller} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
        <div className="ledger-table" role="table" aria-rowcount={rows.length}>
          <div className="lrow lhead" role="row">
            {COLUMNS.map((c) => (
              <span key={c.key} className={c.numeric ? "num" : undefined} role="columnheader">
                <button
                  type="button"
                  onClick={() => toggleSort(c.key)}
                  aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                >
                  {c.title}
                  {sort.key === c.key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </button>
              </span>
            ))}
          </div>
          <div style={{ height: rows.length * ROW_H, position: "relative" }}>
            {rows.slice(start, end).map((p, i) => (
              <div
                key={p.index}
                role="row"
                tabIndex={0}
                className={`lrow${p.index === selected ? " is-selected" : ""}${p.index === hovered ? " is-hovered" : ""}`}
                onMouseEnter={() => onHover(p.index)}
                onMouseLeave={() => onHover(null)}
                style={{ position: "absolute", top: (start + i) * ROW_H, left: 0, right: 0 }}
                onClick={() => onPick(p.index)}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onPick(p.index))}
              >
                <span className="num">{p.index}</span>
                <span title={p.name ?? undefined}>{p.name ?? <i style={{ color: "var(--text-3)" }}>unnamed</i>}</span>
                <span title={p.layer ?? undefined}>{p.layer ?? "—"}</span>
                <span>{p.geometry_type ?? "—"}</span>
                <span>
                  <span className={`dot ${p.status}`} /> {label(p.status)}
                </span>
                <span className="num">{p.area_m2 != null ? fmt(area(p.area_m2, units)) : ""}</span>
                <span className="num">{p.perimeter_m != null ? fmt(length(p.perimeter_m, units)) : ""}</span>
                <span className="num">{p.length_m != null ? fmt(length(p.length_m, units)) : ""}</span>
              </div>
            ))}
            {!rows.length && <p className="empty-note" style={{ padding: "14px" }}>No features match.</p>}
          </div>
        </div>
      </div>
    </section>
  );
}
