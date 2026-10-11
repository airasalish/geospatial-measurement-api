import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type GeoFile, type MapCollection, type MeasurementSummary } from "./api";
import { Inspector } from "./components/Inspector";
import { Ledger } from "./components/Ledger";
import { type DrawKind, type FocusRequest, type LngLat, MapView } from "./components/MapView";
import { DrawHud, DropVeil, EmptyState, MapControls, Readout } from "./components/Overlays";
import { FileList, Logo, SampleList, UploadZone, type UploadState } from "./components/Sidebar";
import { baseName, downloadText, measurementsCsv, sketchKml } from "./exporters";
import type { UnitSystem } from "./units";

/** Ledger height: 300px, but never more than a third of short screens. */
function useLedgerHeight(): number {
  const calc = () => Math.round(Math.max(180, Math.min(300, window.innerHeight * 0.34)));
  const [h, setH] = useState(calc);
  useEffect(() => {
    const onResize = () => setH(calc());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return h;
}

function readUrl(): { file: string | null; feature: number | null; satellite: boolean } {
  const q = new URLSearchParams(window.location.search);
  const feature = q.get("feature");
  return {
    file: q.get("file"),
    feature: feature != null && /^\d+$/.test(feature) ? Number(feature) : null,
    satellite: q.get("basemap") === "satellite",
  };
}

function stored<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode etc. */
  }
}

interface Active {
  file: GeoFile;
  collection: MapCollection | null;
  summary: MeasurementSummary | null;
  truncated: boolean;
}

export default function App() {
  const [files, setFiles] = useState<GeoFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(true);
  const [active, setActive] = useState<Active | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [selected, setSelected] = useState<number | null>(readUrl().feature);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [units, setUnits] = useState<UnitSystem>(() => stored("units", "metric", ["metric", "imperial"] as const));
  const [basemap, setBasemap] = useState<"map" | "satellite">(() =>
    readUrl().satellite ? "satellite" : stored("basemap", "map", ["map", "satellite"] as const),
  );
  const [showLabels, setShowLabels] = useState(true);
  const [ledgerOpen, setLedgerOpen] = useState(true);
  const [hovered, setHovered] = useState<number | null>(null);
  const [draw, setDraw] = useState<DrawKind | null>(null);
  const [sketch, setSketch] = useState<{ vertices: LngLat[]; cursor: LngLat | null }>({ vertices: [], cursor: null });
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const LEDGER_H = useLedgerHeight();
  const openSeq = useRef(0);

  const refreshFiles = useCallback(async () => {
    try {
      setFiles((await api.listFiles()).items);
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    } finally {
      setFilesLoading(false);
    }
  }, []);

  const openFile = useCallback(async (id: string, feature: number | null = null) => {
    const seq = ++openSeq.current; // ignore responses for files the user has already left
    setLoadingFile(true);
    setSelected(feature);
    try {
      const file = await api.getFile(id);
      if (seq !== openSeq.current) return;
      if (file.status !== "COMPLETED") {
        setActive({ file, collection: null, summary: null, truncated: false });
        return;
      }
      const [geo, m] = await Promise.all([api.geojson(id), api.measurements(id)]);
      if (seq !== openSeq.current) return;
      setActive({ file, collection: geo.collection, summary: m.summary, truncated: geo.truncated });
      if (feature != null) setFocus({ index: feature, nonce: Date.now() });
    } catch (e) {
      if (seq !== openSeq.current) return;
      if (e instanceof ApiError) setError(e);
      setActive(null);
    } finally {
      if (seq === openSeq.current) setLoadingFile(false);
    }
  }, []);

  // initial load + deep link
  useEffect(() => {
    void refreshFiles();
    const { file, feature } = readUrl();
    if (file) void openFile(file, feature);
  }, [refreshFiles, openFile]);

  // keep ?file=&feature= in the URL so views can be shared
  useEffect(() => {
    const q = new URLSearchParams();
    if (active) q.set("file", active.file.id);
    if (active && selected != null) q.set("feature", String(selected));
    if (active && basemap === "satellite") q.set("basemap", "satellite");
    const next = q.toString() ? `?${q}` : window.location.pathname;
    window.history.replaceState(null, "", next);
  }, [active, selected, basemap]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const doUpload = async (blob: File | Blob, name: string) => {
    setError(null);
    setUpload({ name, progress: 0, phase: "uploading" });
    try {
      const file = await api.upload(blob, name, (p) =>
        setUpload({ name, progress: p, phase: p >= 1 ? "processing" : "uploading" }),
      );
      await openFile(file.id);
      void refreshFiles();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(String(e), 0, "error"));
      void refreshFiles();
    } finally {
      setUpload(null);
    }
  };

  const doUploadRef = useRef(doUpload);
  doUploadRef.current = doUpload;

  const uploadSample = async (name: string) => {
    try {
      const res = await fetch(`/samples/${name}`);
      if (!res.ok) throw new ApiError(`Sample ${name} is not available`, res.status, "sample_missing");
      await doUpload(await res.blob(), name);
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  };

  const startDraw = (kind: DrawKind | null) => {
    setSketch({ vertices: [], cursor: null });
    setDraw(kind);
    if (kind) setSelected(null);
  };

  /** A finished sketch goes through the real pipeline as a one-feature KML file. */
  const submitSketch = (vertices: LngLat[], kind: DrawKind) => {
    setDraw(null);
    const stamp = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const name = `Sketch ${kind === "polygon" ? "area" : "line"} ${stamp}`;
    const kml = sketchKml(vertices, kind, name);
    void doUpload(new Blob([kml], { type: "application/vnd.google-earth.kml+xml" }), `${name.replace(/[ :]/g, "-")}.kml`);
  };

  // Drop a file anywhere on the window, not just on the upload box.
  useEffect(() => {
    let depth = 0;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth += 1;
      setDragging(true);
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) setDragging(false);
    };
    const over = (e: DragEvent) => hasFiles(e) && e.preventDefault();
    const drop = (e: DragEvent) => {
      depth = 0;
      setDragging(false);
      if (e.defaultPrevented) return; // already handled by the upload box
      e.preventDefault();
      const file = e.dataTransfer?.files[0];
      if (file) void doUploadRef.current(file, file.name);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, []);

  const deleteFile = async (id: string) => {
    try {
      await api.deleteFile(id);
      if (active?.file.id === id) {
        setActive(null);
        setSelected(null);
      }
      await refreshFiles();
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  };

  const pick = (index: number | null, zoom = false) => {
    setSelected(index);
    if (index != null && zoom) setFocus({ index, nonce: Date.now() });
  };

  const collection = active?.collection ?? null;
  const showLedger = Boolean(collection && collection.features.length);
  const inspecting = Boolean(active && selected != null && collection && selected < collection.features.length);
  const ledgerSpace = showLedger && ledgerOpen ? LEDGER_H + 14 : showLedger ? 50 : 0;

  return (
    <div
      className={`app${inspecting ? " has-inspector" : ""}${showLedger && ledgerOpen ? " has-ledger" : ""}`}
      style={{ ["--ledger-h" as string]: `${LEDGER_H}px` }}
    >
      <aside className="sidebar">
        <div className="brand">
          <Logo />
          <div>
            <h1>
              Traverse<em>.</em>
            </h1>
            <p>Survey-grade measurement</p>
          </div>
        </div>
        <div className="section">
          <h2 className="section-label">
            <span>
              <b>01</b>Upload
            </span>
          </h2>
          <UploadZone
            upload={upload}
            error={error}
            onFile={(f) => void doUpload(f, f.name)}
            onOpenFailed={(id) => {
              setError(null);
              void openFile(id);
            }}
            onDismissError={() => setError(null)}
          />
        </div>
        <div className="section">
          <h2 className="section-label">
            <span>
              <b>02</b>Sample data
            </span>
            <span>no files? use these</span>
          </h2>
          <SampleList busy={!!upload} onOpen={(s) => void uploadSample(s)} />
        </div>
        <div className="section files-section">
          <h2 className="section-label">
            <span>
              <b>03</b>Files
            </span>
            <span>{files.length || ""}</span>
          </h2>
          <FileList
            files={files}
            activeId={active?.file.id ?? null}
            loading={filesLoading}
            onOpen={(id) => void openFile(id)}
            onDelete={(id) => void deleteFile(id)}
          />
        </div>
        <footer className="sidebar-foot">
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
          <span>LAEA area · AEQD length · geodesic edges</span>
        </footer>
      </aside>

      <main className="stage">
        <MapView
          collection={collection}
          bbox={active?.file.bbox ?? null}
          fileKey={active?.file.id ?? null}
          selected={selected}
          focus={focus}
          onSelect={(i) => pick(i)}
          basemap={basemap}
          showLabels={showLabels}
          units={units}
          hovered={hovered}
          onHover={setHovered}
          draw={draw}
          onDrawChange={(vertices, cursor) => setSketch({ vertices, cursor })}
          onDrawFinish={submitSketch}
          onDrawCancel={() => startDraw(null)}
          padding={{
            top: 110,
            left: 40,
            right: inspecting ? 420 : 60,
            bottom: ledgerSpace + 40,
          }}
        />

        {draw && (
          <DrawHud
            kind={draw}
            vertices={sketch.vertices}
            cursor={sketch.cursor}
            units={units}
            onFinish={() => submitSketch(sketch.vertices, draw)}
            onCancel={() => startDraw(null)}
          />
        )}

        {active ? (
          <Readout
            file={active.file}
            summary={active.summary}
            units={units}
            onUnits={(u) => {
              setUnits(u);
              store("units", u);
            }}
            onExportCsv={() =>
              collection &&
              downloadText(measurementsCsv(collection), `${baseName(active.file.filename)}-measurements.csv`, "text/csv")
            }
            geojsonHref={api.geojsonUrl(active.file.id)}
            truncated={active.truncated}
          />
        ) : (
          !loadingFile &&
          !draw && (
            <EmptyState onSample={(s) => void uploadSample(s)} onBrowse={() => fileInput.current?.click()} busy={!!upload} />
          )
        )}

        {active?.file.status === "FAILED" && (
          <div className="overlay toast-warn" role="status" style={{ borderColor: "rgba(180,56,42,.45)" }}>
            <strong>Processing failed.</strong> {active.file.error}
          </div>
        )}
        {active && active.file.warnings.length > 0 && active.file.status === "COMPLETED" && (
          <div className="overlay toast-warn" role="status">
            {active.file.warnings.join(" ")}
          </div>
        )}
        {active?.truncated && (
          <div className="overlay toast-warn" role="status" style={{ top: 150 }}>
            Showing the first {collection?.features.length.toLocaleString()} features on the map; totals cover all.
          </div>
        )}

        <MapControls
          basemap={basemap}
          onBasemap={(b) => (setBasemap(b), store("basemap", b))}
          showLabels={showLabels}
          onLabels={setShowLabels}
          draw={draw}
          onDraw={startDraw}
          disabled={!!upload}
        />

        {inspecting && active && selected != null && (
          <Inspector
            fileId={active.file.id}
            index={selected}
            name={collection?.features[selected]?.properties.name ?? null}
            units={units}
            onClose={() => setSelected(null)}
            onZoom={() => pick(selected, true)}
          />
        )}

        {showLedger && collection && (
          <Ledger
            collection={collection}
            units={units}
            selected={selected}
            hovered={hovered}
            onHover={setHovered}
            onPick={(i) => pick(i, true)}
            open={ledgerOpen}
            onToggle={() => setLedgerOpen((o) => !o)}
            height={LEDGER_H}
          />
        )}

        {dragging && <DropVeil />}

        {loadingFile && (
          <div className="loading-veil" aria-live="polite">
            <span className="pill">Loading features…</span>
          </div>
        )}
      </main>

      {/* hidden input for the empty-state "Upload a file" button */}
      <input
        ref={fileInput}
        type="file"
        accept=".zip,.kml,.kmz"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void doUpload(f, f.name);
          e.target.value = "";
        }}
      />
    </div>
  );
}
