import { useCallback, useEffect, useRef, useState } from "react";
import {
  account,
  api,
  ApiError,
  type BaseSpec,
  type BatchVolumes,
  type GeoFile,
  isRasterName,
  type MapCollection,
  type MeasurementSummary,
  type ProfileResult,
  type Project,
  type Raster,
  terrain,
  type TerrainTarget,
  uploadTo,
  type User,
  type VolumeResult,
} from "./api";
import { getToken, setProject, setToken } from "./auth";
import { LoginScreen, ProjectBar } from "./components/Account";
import { Inspector } from "./components/Inspector";
import { Ledger } from "./components/Ledger";
import { type DemLayer, type DrawKind, type FitRequest, type FocusRequest, type LngLat, MapView } from "./components/MapView";
import { DrawHud, DropVeil, EmptyState, MapControls, Readout } from "./components/Overlays";
import { FileList, Logo, SampleList, UploadZone, type UploadState } from "./components/Sidebar";
import { DemList, ProfilePanel, StockpileReport, TerrainBar, VolumePanel } from "./components/Terrain";
import { baseName, downloadText, measurementsCsv, sketchKml, stockpileCsv } from "./exporters";
import type { BBox } from "./geo";
import type { UnitSystem } from "./units";

/** Bottom-panel height: 300px, but never more than a third of short screens. */
function usePanelHeight(): number {
  const calc = () => Math.round(Math.max(180, Math.min(300, window.innerHeight * 0.34)));
  const [h, setH] = useState(calc);
  useEffect(() => {
    const onResize = () => setH(calc());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return h;
}

function readUrl() {
  const q = new URLSearchParams(window.location.search);
  const feature = q.get("feature");
  return {
    file: q.get("file"),
    dem: q.get("dem"),
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

const errMessage = (e: unknown) => (e instanceof Error ? e.message : String(e));

interface Active {
  file: GeoFile;
  collection: MapCollection | null;
  summary: MeasurementSummary | null;
  truncated: boolean;
}

interface Analysis<T> {
  title: string;
  target: TerrainTarget;
  geometry: GeoJSON.Geometry | null;
  result: T | null;
  loading: boolean;
  error: string | null;
}

type DrawPurpose = "sketch" | "volume" | "profile";
type Bottom = "ledger" | "profile" | "report";

/** The quarry sample is three files: an earlier survey, the latest survey and outlines. */
const QUARRY_SAMPLE = ["quarry_dsm_2026-01.tif", "quarry_dsm_2026-03.tif", "quarry_outlines.kml"];

export default function App() {
  // ---- accounts (only when the deployment sets GEO_AUTH_REQUIRED=true)
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProjectState] = useState<string | null>(null);
  const ready = authRequired === false || (authRequired === true && user !== null);

  // ---- vector files
  const [files, setFiles] = useState<GeoFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(true);
  const [active, setActive] = useState<Active | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [selected, setSelected] = useState<number | null>(readUrl().feature);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [fit, setFit] = useState<FitRequest | null>(null);

  // ---- terrain
  const [dems, setDems] = useState<Raster[]>([]);
  const [dem, setDem] = useState<Raster | null>(null);
  const [demLayer, setDemLayer] = useState<Pick<DemLayer, "style" | "opacity">>({ style: "elevation", opacity: 0.85 });
  const [vol, setVol] = useState<Analysis<VolumeResult> | null>(null);
  const [volBase, setVolBase] = useState<BaseSpec>({ method: "triangulated" });
  const [prof, setProf] = useState<Analysis<ProfileResult> | null>(null);
  const [report, setReport] = useState<{ result: BatchVolumes | null; loading: boolean; error: string | null } | null>(null);
  const [reportBase, setReportBase] = useState<BaseSpec>({ method: "triangulated" });
  const [marker, setMarker] = useState<LngLat | null>(null);
  const [cursorElev, setCursorElev] = useState<number | null>(null);
  const [view3d, setView3d] = useState(false);
  const [exaggeration, setExaggeration] = useState(2);

  // ---- UI
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [units, setUnits] = useState<UnitSystem>(() => stored("units", "metric", ["metric", "imperial"] as const));
  const [basemap, setBasemap] = useState<"map" | "satellite">(() =>
    readUrl().satellite ? "satellite" : stored("basemap", "map", ["map", "satellite"] as const),
  );
  const [showLabels, setShowLabels] = useState(true);
  const [ledgerOpen, setLedgerOpen] = useState(true);
  const [bottom, setBottom] = useState<Bottom>("ledger");
  const [hovered, setHovered] = useState<number | null>(null);
  const [draw, setDraw] = useState<DrawKind | null>(null);
  const [drawPurpose, setDrawPurpose] = useState<DrawPurpose>("sketch");
  const [sketch, setSketch] = useState<{ vertices: LngLat[]; cursor: LngLat | null }>({ vertices: [], cursor: null });
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const PANEL_H = usePanelHeight();
  const openSeq = useRef(0);
  const volAbort = useRef<AbortController | null>(null);
  const profAbort = useRef<AbortController | null>(null);
  const reportAbort = useRef<AbortController | null>(null);
  const cursorAbort = useRef<AbortController | null>(null);
  const cursorTimer = useRef<number | undefined>(undefined);

  const fitTo = (bbox: BBox | null | undefined) => bbox && setFit({ bbox, nonce: Date.now() });

  // ------------------------------------------------------------- loading

  const refreshFiles = useCallback(async () => {
    try {
      setFiles((await api.listFiles()).items);
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    } finally {
      setFilesLoading(false);
    }
  }, []);

  const refreshDems = useCallback(async () => {
    try {
      setDems((await terrain.list()).items);
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  }, []);

  const openFile = useCallback(async (id: string, feature: number | null = null, fitView = true) => {
    const seq = ++openSeq.current; // ignore responses for files the user has already left
    setLoadingFile(true);
    setSelected(feature);
    setReport(null);
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
      else if (fitView) fitTo(file.bbox);
    } catch (e) {
      if (seq !== openSeq.current) return;
      if (e instanceof ApiError) setError(e);
      setActive(null);
    } finally {
      if (seq === openSeq.current) setLoadingFile(false);
    }
  }, []);

  const openDem = useCallback(async (id: string, fitView = true) => {
    try {
      const r = await terrain.get(id);
      setDem(r);
      setVol(null);
      setProf(null);
      setReport(null);
      if (fitView) fitTo(r.bbox);
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  }, []);

  // auth handshake: open deployments skip straight to the app
  useEffect(() => {
    account
      .config()
      .then(async ({ auth_required }) => {
        if (auth_required && getToken()) {
          try {
            setUser(await account.me());
          } catch {
            setToken(null);
          }
        }
        setAuthRequired(auth_required);
      })
      .catch(() => setAuthRequired(false));
  }, []);

  useEffect(() => {
    if (user) account.projects().then(setProjects).catch(() => undefined);
  }, [user]);

  // initial load + deep link, once allowed in
  useEffect(() => {
    if (!ready) return;
    void refreshFiles();
    void refreshDems();
    const { file, feature, dem: demId } = readUrl();
    if (file) void openFile(file, feature);
    if (demId) void openDem(demId, !file);
  }, [ready, refreshFiles, refreshDems, openFile, openDem]);

  const signOut = () => {
    setToken(null);
    setProject(null);
    setProjectState(null);
    setUser(null);
    setActive(null);
    setDem(null);
    setFiles([]);
    setDems([]);
  };

  const chooseProject = (id: string | null) => {
    setProject(id);
    setProjectState(id);
    setActive(null);
    setDem(null);
    void refreshFiles();
    void refreshDems();
  };

  // A 401 anywhere means the session ended: back to sign-in.
  useEffect(() => {
    if (error?.status === 401 && authRequired) signOut();
  }, [error, authRequired]);

  // keep the view in the URL so it can be shared
  useEffect(() => {
    const q = new URLSearchParams();
    if (active) q.set("file", active.file.id);
    if (dem) q.set("dem", dem.id);
    if (active && selected != null) q.set("feature", String(selected));
    if (basemap === "satellite" && (active || dem)) q.set("basemap", "satellite");
    const next = q.toString() ? `?${q}` : window.location.pathname;
    window.history.replaceState(null, "", next);
  }, [active, dem, selected, basemap]);

  // ------------------------------------------------------------- uploads

  const doUpload = async (blob: File | Blob, name: string, open = true) => {
    setError(null);
    setUpload({ name, progress: 0, phase: "uploading" });
    const onProgress = (p: number) => setUpload({ name, progress: p, phase: p >= 1 ? "processing" : "uploading" });
    try {
      if (isRasterName(name)) {
        const r = await uploadTo<Raster>("/api/rasters/", blob, name, onProgress);
        await refreshDems();
        if (open) await openDem(r.id, !active);
      } else {
        const file = await api.upload(blob, name, onProgress);
        if (open) await openFile(file.id);
        void refreshFiles();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(String(e), 0, "error"));
      void refreshFiles();
      void refreshDems();
    } finally {
      setUpload(null);
      if (user) account.projects().then(setProjects).catch(() => undefined); // refresh counts
    }
  };

  const doUploadRef = useRef(doUpload);
  doUploadRef.current = doUpload;

  const fetchSample = async (name: string) => {
    const res = await fetch(`/samples/${name}`);
    if (!res.ok) throw new ApiError(`Sample ${name} is not available`, res.status, "sample_missing");
    return res.blob();
  };

  const uploadSample = async (name: string) => {
    try {
      if (name === "quarry") {
        // Earlier survey first (not opened), then the latest survey and the outlines.
        for (const [i, f] of QUARRY_SAMPLE.entries()) await doUpload(await fetchSample(f), f, i > 0);
        return;
      }
      await doUpload(await fetchSample(name), name);
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  };

  // ------------------------------------------------------------- drawing

  const startDraw = (kind: DrawKind | null, purpose: DrawPurpose = "sketch") => {
    setSketch({ vertices: [], cursor: null });
    setDraw(kind);
    setDrawPurpose(purpose);
    if (kind) setSelected(null);
  };

  /** A finished sketch goes through the real pipeline as a one-feature KML file. */
  const submitSketch = (vertices: LngLat[], kind: DrawKind) => {
    const stamp = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const name = `Sketch ${kind === "polygon" ? "area" : "line"} ${stamp}`;
    const kml = sketchKml(vertices, kind, name);
    void doUpload(new Blob([kml], { type: "application/vnd.google-earth.kml+xml" }), `${name.replace(/[ :]/g, "-")}.kml`);
  };

  const finishDraw = (vertices: LngLat[], kind: DrawKind) => {
    const purpose = drawPurpose;
    setDraw(null);
    if (purpose === "volume" && kind === "polygon") {
      const geometry: GeoJSON.Polygon = { type: "Polygon", coordinates: [[...vertices, vertices[0]!]] };
      runVolume({ geometry }, "Drawn polygon", geometry);
    } else if (purpose === "profile" && kind === "line") {
      const geometry: GeoJSON.LineString = { type: "LineString", coordinates: vertices };
      runProfile({ geometry }, "Drawn line", geometry);
    } else {
      submitSketch(vertices, kind);
    }
  };

  // ------------------------------------------------------------- terrain analyses

  const runVolume = (target: TerrainTarget, title: string, geometry: GeoJSON.Geometry | null, base = volBase) => {
    if (!dem) return;
    volAbort.current?.abort();
    const ctrl = (volAbort.current = new AbortController());
    setSelected(null);
    setVol({ title, target, geometry, result: null, loading: true, error: null });
    terrain
      .volume(dem.id, target, base, ctrl.signal)
      .then((result) => setVol((v) => v && { ...v, result, loading: false }))
      .catch((e) => !ctrl.signal.aborted && setVol((v) => v && { ...v, loading: false, error: errMessage(e) }));
  };

  const runProfile = (target: TerrainTarget, title: string, geometry: GeoJSON.Geometry | null) => {
    if (!dem) return;
    profAbort.current?.abort();
    const ctrl = (profAbort.current = new AbortController());
    setBottom("profile");
    setProf({ title, target, geometry, result: null, loading: true, error: null });
    terrain
      .profile(dem.id, target, ctrl.signal)
      .then((result) => setProf((p) => p && { ...p, result, loading: false }))
      .catch((e) => !ctrl.signal.aborted && setProf((p) => p && { ...p, loading: false, error: errMessage(e) }));
  };

  const runReport = (base = reportBase) => {
    if (!dem || !active) return;
    reportAbort.current?.abort();
    const ctrl = (reportAbort.current = new AbortController());
    setBottom("report");
    setReport({ result: null, loading: true, error: null });
    terrain
      .volumes(dem.id, active.file.id, base, ctrl.signal)
      .then((result) => setReport({ result, loading: false, error: null }))
      .catch((e) => !ctrl.signal.aborted && setReport({ result: null, loading: false, error: errMessage(e) }));
  };

  const volumeForFeature = (index: number) => {
    if (!active?.collection) return;
    const f = active.collection.features[index];
    const name = f?.properties.name ?? `Feature #${index}`;
    runVolume({ feature: { file_id: active.file.id, index } }, name, f?.geometry ?? null);
  };

  const profileForFeature = (index: number) => {
    if (!active?.collection) return;
    const f = active.collection.features[index];
    runProfile({ feature: { file_id: active.file.id, index } }, f?.properties.name ?? `Feature #${index}`, f?.geometry ?? null);
  };

  // Live elevation under the cursor (debounced, cancelled when the cursor moves on).
  const onCursor = (p: LngLat | null) => {
    if (!dem) return;
    window.clearTimeout(cursorTimer.current);
    if (!p) return setCursorElev(null);
    cursorTimer.current = window.setTimeout(() => {
      cursorAbort.current?.abort();
      const ctrl = (cursorAbort.current = new AbortController());
      terrain
        .altitude(dem.id, p[0], p[1], ctrl.signal)
        .then((r) => setCursorElev(r.elevation_m))
        .catch(() => undefined);
    }, 120);
  };

  const closeDem = () => {
    setView3d(false);
    setDem(null);
    setVol(null);
    setProf(null);
    setReport(null);
    setBottom("ledger");
    setCursorElev(null);
  };

  const deleteDem = async (id: string) => {
    try {
      await terrain.remove(id);
      if (dem?.id === id) closeDem();
      await refreshDems();
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  };

  // ------------------------------------------------------------- window drop + keys

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || draw) return;
      setSelected(null);
      setVol(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [draw]);

  const deleteFile = async (id: string) => {
    try {
      await api.deleteFile(id);
      if (active?.file.id === id) {
        setActive(null);
        setSelected(null);
        setReport(null);
      }
      await refreshFiles();
    } catch (e) {
      if (e instanceof ApiError) setError(e);
    }
  };

  const pick = (index: number | null, zoom = false) => {
    setSelected(index);
    if (index != null) setVol(null);
    if (index != null && zoom) setFocus({ index, nonce: Date.now() });
  };

  // ------------------------------------------------------------- layout

  const collection = active?.collection ?? null;
  const hasFeatures = Boolean(collection && collection.features.length);
  const bottomKind: Bottom | null =
    bottom === "profile" && prof ? "profile" : bottom === "report" && report ? "report" : hasFeatures ? "ledger" : null;
  const bottomOpen = bottomKind === "ledger" ? ledgerOpen : bottomKind !== null;
  const inspecting = Boolean(active && selected != null && collection && selected < collection.features.length);
  const sidePanel = inspecting || Boolean(vol);
  const bottomSpace = bottomKind && bottomOpen ? PANEL_H + 14 : bottomKind ? 50 : 0;
  const selectedType = selected != null ? collection?.features[selected]?.properties.geometry_type : null;
  const surveys = dems.filter((d) => d.id !== dem?.id && d.status === "COMPLETED");
  const polygonsInFile = Boolean(
    collection?.features.some((f) => f.properties.geometry_type === "Polygon" || f.properties.geometry_type === "MultiPolygon"),
  );

  if (authRequired === null) return <div className="loading-veil"><span className="pill">Loading…</span></div>;
  if (authRequired && !user) return <LoginScreen onSignedIn={setUser} />;

  return (
    <div
      className={`app${sidePanel ? " has-inspector" : ""}${bottomKind && bottomOpen ? " has-ledger" : ""}`}
      style={{ ["--ledger-h" as string]: `${PANEL_H}px` }}
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
        {user && (
          <ProjectBar
            user={user}
            projects={projects}
            current={project}
            onSelect={chooseProject}
            onCreate={(name) =>
              account
                .createProject(name)
                .then((p) => {
                  setProjects((ps) => [...ps, p].sort((a, b) => a.name.localeCompare(b.name)));
                  chooseProject(p.id);
                })
                .catch((e) => e instanceof ApiError && setError(e))
            }
            onSignOut={signOut}
          />
        )}
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
        <div className="section dem-section">
          <h2 className="section-label">
            <span>
              <b>03</b>Elevation models
            </span>
            <span>{dems.length || ""}</span>
          </h2>
          <DemList dems={dems} activeId={dem?.id ?? null} onOpen={(id) => void openDem(id)} onDelete={(id) => void deleteDem(id)} />
        </div>
        <div className="section files-section">
          <h2 className="section-label">
            <span>
              <b>04</b>Vector files
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
          <span>LAEA area · AEQD length · DEM volumetrics</span>
        </footer>
      </aside>

      <main className="stage">
        <MapView
          collection={collection}
          fit={fit}
          dem={dem ? { id: dem.id, bounds: dem.bbox, ...demLayer } : null}
          overlay={vol?.geometry ?? (bottomKind === "profile" ? (prof?.geometry ?? null) : null)}
          marker={marker}
          terrain3d={dem && view3d ? { id: dem.id, exaggeration, bounds: dem.bbox } : null}
          onCursor={onCursor}
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
          onDrawFinish={finishDraw}
          onDrawCancel={() => startDraw(null)}
          padding={{ top: 110, left: dem ? 340 : 40, right: sidePanel ? 420 : 60, bottom: bottomSpace + 40 }}
        />

        {draw && (
          <DrawHud
            kind={draw}
            vertices={sketch.vertices}
            cursor={sketch.cursor}
            units={units}
            onFinish={() => finishDraw(sketch.vertices, draw)}
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
          !draw &&
          !dem && (
            <EmptyState onSample={(s) => void uploadSample(s)} onBrowse={() => fileInput.current?.click()} busy={!!upload} />
          )
        )}

        {dem && !draw && (
          <TerrainBar
            dem={dem}
            layer={{ id: dem.id, bounds: dem.bbox, ...demLayer }}
            onLayer={(l) => setDemLayer({ style: l.style, opacity: l.opacity })}
            tool={drawPurpose !== "sketch" && draw ? drawPurpose : null}
            onTool={(t) => (t ? startDraw(t === "volume" ? "polygon" : "line", t) : startDraw(null))}
            canReport={polygonsInFile}
            onReport={() => runReport()}
            onClose={closeDem}
            cursorElevation={cursorElev}
            top={active ? 104 : 14}
            view3d={view3d}
            onView3d={setView3d}
            exaggeration={exaggeration}
            onExaggeration={setExaggeration}
          />
        )}

        {active?.file.status === "FAILED" && (
          <div className="overlay toast-warn" role="status" style={{ borderColor: "rgba(180,56,42,.45)" }}>
            <strong>Processing failed.</strong> {active.file.error}
          </div>
        )}

        <MapControls
          basemap={basemap}
          onBasemap={(b) => (setBasemap(b), store("basemap", b))}
          showLabels={showLabels}
          onLabels={setShowLabels}
          draw={drawPurpose === "sketch" ? draw : null}
          onDraw={(k) => startDraw(k, "sketch")}
          disabled={!!upload}
        />

        {inspecting && active && selected != null && !vol && (
          <Inspector
            fileId={active.file.id}
            index={selected}
            name={collection?.features[selected]?.properties.name ?? null}
            units={units}
            onClose={() => setSelected(null)}
            onZoom={() => pick(selected, true)}
            onVolume={dem && (selectedType === "Polygon" || selectedType === "MultiPolygon") ? () => volumeForFeature(selected) : undefined}
            onProfile={dem && selectedType === "LineString" ? () => profileForFeature(selected) : undefined}
          />
        )}

        {vol && (
          <VolumePanel
            title={vol.title}
            result={vol.result}
            loading={vol.loading}
            error={vol.error}
            base={volBase}
            onBase={(b) => {
              setVolBase(b);
              const ready = b.method !== "custom" || b.elevation != null;
              if (ready) runVolume(vol.target, vol.title, vol.geometry, b);
            }}
            surveys={surveys}
            units={units}
            onClose={() => setVol(null)}
          />
        )}

        {bottomKind === "ledger" && collection && (
          <Ledger
            collection={collection}
            units={units}
            selected={selected}
            hovered={hovered}
            onHover={setHovered}
            onPick={(i) => pick(i, true)}
            open={ledgerOpen}
            onToggle={() => setLedgerOpen((o) => !o)}
            height={PANEL_H}
          />
        )}

        {bottomKind === "profile" && prof && (
          <ProfilePanel
            result={prof.result}
            loading={prof.loading}
            error={prof.error}
            title={prof.title}
            units={units}
            height={PANEL_H}
            onHoverPoint={setMarker}
            onClose={() => {
              setProf(null);
              setMarker(null);
              setBottom("ledger");
            }}
          />
        )}

        {bottomKind === "report" && report && dem && active && (
          <StockpileReport
            report={report.result}
            loading={report.loading}
            error={report.error}
            base={reportBase}
            onBase={(b) => {
              setReportBase(b);
              if (b.method !== "custom" || b.elevation != null) runReport(b);
            }}
            surveys={surveys}
            units={units}
            height={PANEL_H}
            demName={dem.filename}
            fileName={active.file.filename}
            onPick={(i) => volumeForFeature(i)}
            onExport={() =>
              report.result &&
              downloadText(
                stockpileCsv(report.result, { dem: dem.filename, file: active.file.filename, base: reportBase.method }),
                `${baseName(active.file.filename)}-stockpile-report.csv`,
                "text/csv",
              )
            }
            onClose={() => {
              setReport(null);
              setBottom("ledger");
            }}
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
        accept=".zip,.kml,.kmz,.tif,.tiff"
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
