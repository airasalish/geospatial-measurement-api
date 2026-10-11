import maplibregl, { type ExpressionSpecification, type GeoJSONSource, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MapCollection, MapFeatureProps } from "../api";
import { type BBox, geometryBounds, isLineal, isPolygonal } from "../geo";
import { area, fmt, length, type UnitSystem } from "../units";

// Free, keyless basemaps: OpenFreeMap vector tiles, and Esri World Imagery for the
// satellite view (drone surveys are easier to judge against imagery).
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const SATELLITE_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

const SOURCE = "features";
const INTERACTIVE = ["poly-fill", "line", "point"];

const POLYGON: ExpressionSpecification = ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false];
const LINE: ExpressionSpecification = ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false];
const POINT: ExpressionSpecification = ["match", ["geometry-type"], ["Point", "MultiPoint"], true, false];

const statusColor = (measured: string): ExpressionSpecification => [
  "match", ["get", "status"],
  "FAILED", "#b4382a",
  "UNSUPPORTED", "#b98318",
  "NOT_APPLICABLE", "#1c1f1d",
  measured,
];
const isOn = (key: "hover" | "selected"): ExpressionSpecification => ["boolean", ["feature-state", key], false];

export type DrawKind = "polygon" | "line";
export type LngLat = [number, number];

export interface FocusRequest {
  index: number;
  nonce: number;
}

interface Props {
  collection: MapCollection | null;
  bbox: BBox | null;
  fileKey: string | null;
  selected: number | null;
  focus: FocusRequest | null;
  onSelect: (index: number | null) => void;
  basemap: "map" | "satellite";
  showLabels: boolean;
  units: UnitSystem;
  padding: maplibregl.PaddingOptions;
  /** Feature hovered anywhere in the app (map or ledger). */
  hovered: number | null;
  onHover: (index: number | null) => void;
  /** Active sketch tool, if any. */
  draw: DrawKind | null;
  /** Vertices so far, plus the live cursor position, while drawing. */
  onDrawChange: (vertices: LngLat[], cursor: LngLat | null) => void;
  onDrawFinish: (vertices: LngLat[], kind: DrawKind) => void;
  onDrawCancel: () => void;
}

const DRAW = "draw";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/** Sketch preview: the shape so far, the rubber-band segment and the vertices. */
function sketchGeoJSON(kind: DrawKind, vertices: LngLat[], cursor: LngLat | null): GeoJSON.FeatureCollection {
  const pts = cursor ? [...vertices, cursor] : vertices;
  const features: GeoJSON.Feature[] = vertices.map((c, i) => ({
    type: "Feature",
    properties: { first: i === 0 && kind === "polygon" && vertices.length >= 3 },
    geometry: { type: "Point", coordinates: c },
  }));
  if (kind === "polygon" && pts.length >= 3) {
    features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...pts, pts[0]!]] } });
  } else if (pts.length >= 2) {
    features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: pts } });
  }
  return { type: "FeatureCollection", features };
}

interface Hover {
  props: MapFeatureProps;
  x: number;
  y: number;
}

export function MapView(props: Props) {
  const { collection, bbox, fileKey, selected, focus, onSelect, basemap, showLabels, units, padding, hovered, draw } =
    props;
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  const hoveredId = useRef<number | null>(null);
  const selectedId = useRef<number | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const cb = useRef(props);
  cb.current = props; // latest callbacks for map event handlers registered once
  const sketch = useRef<{ kind: DrawKind | null; vertices: LngLat[]; cursor: LngLat | null }>({
    kind: null,
    vertices: [],
    cursor: null,
  });
  const paddingRef = useRef(padding);
  paddingRef.current = padding;

  /** Move the "selected" feature-state from the previous feature to `id`. */
  const applySelected = (id: number | null) => {
    const map = mapRef.current;
    if (!map?.getSource(SOURCE)) return;
    if (selectedId.current != null) map.setFeatureState({ source: SOURCE, id: selectedId.current }, { selected: false });
    if (id != null) map.setFeatureState({ source: SOURCE, id }, { selected: true });
    selectedId.current = id;
  };

  /** Move the "hover" feature-state to `id` (driven by the map or the ledger). */
  const applyHover = (id: number | null) => {
    const map = mapRef.current;
    if (!map?.getSource(SOURCE) || id === hoveredId.current) return;
    if (hoveredId.current != null) map.setFeatureState({ source: SOURCE, id: hoveredId.current }, { hover: false });
    if (id != null) map.setFeatureState({ source: SOURCE, id }, { hover: true });
    hoveredId.current = id;
  };

  const renderSketch = () => {
    const map = mapRef.current;
    const { kind, vertices, cursor } = sketch.current;
    (map?.getSource(DRAW) as GeoJSONSource | undefined)?.setData(kind ? sketchGeoJSON(kind, vertices, cursor) : EMPTY);
  };

  const finishSketch = () => {
    const { kind, vertices } = sketch.current;
    // A double-click also fires two clicks; drop the duplicate vertices they add.
    const clean = vertices.filter((v, i) => i === 0 || v[0] !== vertices[i - 1]![0] || v[1] !== vertices[i - 1]![1]);
    if (!kind || clean.length < (kind === "polygon" ? 3 : 2)) return;
    cb.current.onDrawFinish(clean, kind);
  };

  // ---- one-time map setup
  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: STYLE_URL,
      center: [77.59, 12.97], // Bengaluru
      zoom: 4.2,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-right");
    map.touchZoomRotate.disableRotation();

    map.on("load", () => {
      const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
      map.addSource("satellite", {
        type: "raster",
        tiles: [SATELLITE_TILES],
        tileSize: 256,
        maxzoom: 19,
        attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
      });
      map.addLayer(
        { id: "satellite", type: "raster", source: "satellite", layout: { visibility: "none" } },
        firstSymbol,
      );

      map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "poly-fill",
        type: "fill",
        source: SOURCE,
        filter: POLYGON,
        paint: {
          "fill-color": statusColor("#e4532a"),
          "fill-opacity": ["case", isOn("selected"), 0.42, isOn("hover"), 0.32, 0.18],
        },
      });
      map.addLayer({
        id: "poly-line",
        type: "line",
        source: SOURCE,
        filter: POLYGON,
        paint: {
          "line-color": ["case", isOn("selected"), "#1c1f1d", statusColor("#c4441f")],
          "line-width": ["case", isOn("selected"), 2.6, isOn("hover"), 2.2, 1.4],
        },
      });
      map.addLayer({
        id: "line-casing",
        type: "line",
        source: SOURCE,
        filter: LINE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#faf7f0", "line-width": ["case", isOn("selected"), 7, 5], "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "line",
        type: "line",
        source: SOURCE,
        filter: LINE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["case", isOn("selected"), "#1c1f1d", statusColor("#2b6d8e")],
          "line-width": ["case", isOn("selected"), 4, isOn("hover"), 3.6, 2.6],
        },
      });
      map.addLayer({
        id: "point",
        type: "circle",
        source: SOURCE,
        filter: POINT,
        paint: {
          "circle-radius": ["case", isOn("selected"), 7.5, isOn("hover"), 6.5, 5],
          "circle-color": ["case", isOn("selected"), "#e4532a", statusColor("#1c1f1d")],
          "circle-stroke-color": "#faf7f0",
          "circle-stroke-width": 2,
        },
      });
      const labelPaint = {
        "text-color": "#1c1f1d",
        "text-halo-color": "rgba(250,247,240,0.95)",
        "text-halo-width": 1.6,
      };
      map.addLayer({
        id: "label-area",
        type: "symbol",
        source: SOURCE,
        filter: POLYGON,
        minzoom: 12,
        layout: {
          "text-field": ["get", "_label"],
          "text-font": ["Noto Sans Regular"],
          "text-size": 12,
          "text-padding": 6,
        },
        paint: labelPaint,
      });
      map.addLayer({
        id: "label-line",
        type: "symbol",
        source: SOURCE,
        filter: LINE,
        minzoom: 13,
        layout: {
          "symbol-placement": "line",
          "text-field": ["get", "_label"],
          "text-font": ["Noto Sans Regular"],
          "text-size": 11.5,
          "text-offset": [0, -0.9],
        },
        paint: labelPaint,
      });

      // Sketch layers sit on top of everything.
      map.addSource(DRAW, { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "draw-fill",
        type: "fill",
        source: DRAW,
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": "#2b6d8e", "fill-opacity": 0.18 },
      });
      map.addLayer({
        id: "draw-line",
        type: "line",
        source: DRAW,
        filter: ["!=", ["geometry-type"], "Point"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#1c1f1d", "line-width": 2.2, "line-dasharray": [2, 1.5] },
      });
      map.addLayer({
        id: "draw-vertex",
        type: "circle",
        source: DRAW,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": ["case", ["boolean", ["get", "first"], false], 7, 4.5],
          "circle-color": ["case", ["boolean", ["get", "first"], false], "#e4532a", "#faf7f0"],
          "circle-stroke-color": "#1c1f1d",
          "circle-stroke-width": 2,
        },
      });

      map.on("mousemove", (e) => {
        if (sketch.current.kind) {
          sketch.current.cursor = [e.lngLat.lng, e.lngLat.lat];
          renderSketch();
          cb.current.onDrawChange(sketch.current.vertices, sketch.current.cursor);
          setHover(null);
          return;
        }
        const f: MapGeoJSONFeature | undefined = map.queryRenderedFeatures(e.point, { layers: INTERACTIVE })[0];
        const id = f ? Number(f.id) : null;
        if (id !== hoveredId.current) cb.current.onHover(id);
        map.getCanvas().style.cursor = f ? "pointer" : "";
        setHover(f ? { props: f.properties as MapFeatureProps, x: e.point.x, y: e.point.y } : null);
      });
      map.on("mouseout", () => {
        if (!sketch.current.kind) cb.current.onHover(null);
        setHover(null);
      });
      map.on("click", (e) => {
        const s = sketch.current;
        if (s.kind) {
          // Clicking the first vertex closes a polygon.
          const first = s.vertices[0];
          if (s.kind === "polygon" && first && s.vertices.length >= 3) {
            const pt = map.project(first);
            if (Math.hypot(pt.x - e.point.x, pt.y - e.point.y) < 12) return finishSketch();
          }
          s.vertices = [...s.vertices, [e.lngLat.lng, e.lngLat.lat]];
          renderSketch();
          cb.current.onDrawChange(s.vertices, s.cursor);
          return;
        }
        const f = map.queryRenderedFeatures(e.point, { layers: INTERACTIVE })[0];
        onSelectRef.current(f ? Number(f.id) : null);
      });
      map.on("dblclick", (e) => {
        if (!sketch.current.kind) return;
        e.preventDefault();
        finishSketch();
      });

      setReady(true);
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- hover linking with the ledger
  useEffect(() => {
    if (ready) applyHover(hovered);
  }, [hovered, ready]);

  // ---- sketch tool lifecycle + keyboard (Enter finish, Backspace undo, Esc cancel)
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    sketch.current = { kind: draw, vertices: [], cursor: null };
    renderSketch();
    if (!draw) {
      map.doubleClickZoom.enable();
      map.getCanvas().style.cursor = "";
      return;
    }
    map.doubleClickZoom.disable();
    map.getCanvas().style.cursor = "crosshair";
    applyHover(null);
    const onKey = (e: KeyboardEvent) => {
      const s = sketch.current;
      if (e.key === "Enter") finishSketch();
      else if (e.key === "Escape") cb.current.onDrawCancel();
      else if (e.key === "Backspace" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        s.vertices = s.vertices.slice(0, -1);
        renderSketch();
        cb.current.onDrawChange(s.vertices, s.cursor);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [draw, ready]);

  // ---- data (+ unit-aware labels)
  // Features without a placeable geometry stay in the ledger but can't go on the map.
  const labelled = useMemo<GeoJSON.FeatureCollection | null>(() => {
    if (!collection) return null;
    return {
      type: "FeatureCollection",
      features: collection.features.flatMap((f) => {
        if (!f.geometry) return [];
        const p = f.properties;
        const label = isPolygonal(p.geometry_type)
          ? fmt(area(p.area_m2, units))
          : isLineal(p.geometry_type)
            ? fmt(length(p.length_m, units))
            : "";
        return [{ ...f, geometry: f.geometry, properties: { ...p, _label: label === "—" ? "" : label } }];
      }),
    };
  }, [collection, units]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const source = map.getSource(SOURCE) as GeoJSONSource | undefined;
    source?.setData(labelled ?? { type: "FeatureCollection", features: [] });
    // New data can mean new ids (another file): re-apply selection and hover.
    selectedId.current = null;
    hoveredId.current = null;
    applySelected(selected);
    applyHover(hovered);
  }, [labelled, ready]);

  // ---- fit to the file when it changes
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !bbox) return;
    map.fitBounds(bbox, { padding: paddingRef.current, maxZoom: 18, duration: 900 });
  }, [fileKey, ready]);

  // ---- selection highlight
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    applySelected(selected);
  }, [selected, ready]);

  // ---- explicit "zoom to feature" requests (table clicks, inspector button)
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !focus || !collection) return;
    const feature = collection.features[focus.index];
    const b = geometryBounds(feature?.geometry);
    if (!b) return;
    if (b[0] === b[2] && b[1] === b[3]) {
      map.flyTo({ center: [b[0], b[1]], zoom: Math.max(map.getZoom(), 16), padding: paddingRef.current });
    } else {
      map.fitBounds(b, { padding: paddingRef.current, maxZoom: 18, duration: 700 });
    }
  }, [focus, ready]);

  // ---- basemap + labels toggles
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setLayoutProperty("satellite", "visibility", basemap === "satellite" ? "visible" : "none");
  }, [basemap, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    for (const id of ["label-area", "label-line"]) {
      map.setLayoutProperty(id, "visibility", showLabels ? "visible" : "none");
    }
  }, [showLabels, ready]);

  const tip = hover ? describe(hover.props, units) : null;
  return (
    <>
      <div ref={container} className="map" role="region" aria-label="Map of uploaded features" />
      {hover && tip && (
        <div className="tooltip" style={{ left: hover.x, top: hover.y }}>
          <div className="t-name">{tip.title}</div>
          <div className="t-val">{tip.value}</div>
        </div>
      )}
    </>
  );
}

function describe(p: MapFeatureProps, units: UnitSystem): { title: string; value: string } {
  const title = p.name || `Feature #${p.index}`;
  if (p.status !== "MEASURED") return { title, value: `${p.geometry_type ?? "No geometry"} · ${label(p.status)}` };
  if (isPolygonal(p.geometry_type) || p.area_m2 != null) {
    return { title, value: `${fmt(area(p.area_m2, units))} · ${fmt(length(p.perimeter_m, units))} perimeter` };
  }
  return { title, value: `${fmt(length(p.length_m, units))}` };
}

export const label = (status: string) =>
  ({ MEASURED: "Measured", NOT_APPLICABLE: "Not applicable", UNSUPPORTED: "Unsupported", FAILED: "Failed" })[
    status
  ] ?? status;
