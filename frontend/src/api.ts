// Typed client for the FastAPI backend. Types mirror app/schemas.py.

export type FileStatus = "PROCESSING" | "COMPLETED" | "FAILED";
export type MeasurementStatus = "MEASURED" | "NOT_APPLICABLE" | "UNSUPPORTED" | "FAILED";

export interface GeoFile {
  id: string;
  filename: string;
  format: "shapefile" | "kml" | "kmz";
  size_bytes: number;
  sha256: string;
  status: FileStatus;
  error: string | null;
  crs: string | null;
  feature_count: number;
  layers: string[];
  bbox: [number, number, number, number] | null;
  warnings: string[];
  processing_ms: number | null;
  created_at: string;
  processed_at: string | null;
}

export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export interface Measurement {
  index: number;
  source_id: string | null;
  layer: string | null;
  geometry_type: string | null;
  status: MeasurementStatus;
  area_m2: number | null;
  perimeter_m: number | null;
  length_m: number | null;
  measurement_crs: Record<string, string>;
  note: string | null;
}

export interface MeasurementSummary {
  feature_count: number;
  total_area_m2: number;
  total_perimeter_m: number;
  total_length_m: number;
  by_status: Partial<Record<MeasurementStatus, number>>;
  by_geometry_type: Record<string, number>;
}

export interface MeasurementsResponse extends Page<Measurement> {
  file_id: string;
  summary: MeasurementSummary;
}

export interface FeatureDetail {
  index: number;
  source_id: string | null;
  layer: string | null;
  geometry_type: string | null;
  crs: string | null;
  geometry: GeoJSON.Geometry | null;
  properties: Record<string, unknown>;
}

/** Properties on each feature of the /geojson endpoint. */
export interface MapFeatureProps {
  index: number;
  name: string | null;
  geometry_type: string | null;
  status: MeasurementStatus;
  area_m2: number | null;
  perimeter_m: number | null;
  length_m: number | null;
  layer: string | null;
  source_id: string | null;
}

export type MapCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry | null, MapFeatureProps>;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly fileId?: string,
  ) {
    super(message);
  }
}

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    if (body?.error) return new ApiError(body.error.message, res.status, body.error.code, body.error.file_id);
    if (body?.detail) return new ApiError(JSON.stringify(body.detail), res.status, "validation_error");
  } catch {
    /* not JSON */
  }
  return new ApiError(`Request failed (${res.status})`, res.status, "http_error");
}

async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}

export const api = {
  listFiles: (limit = 50, signal?: AbortSignal) => get<Page<GeoFile>>(`/api/files/?limit=${limit}`, signal),
  getFile: (id: string, signal?: AbortSignal) => get<GeoFile>(`/api/files/${id}/`, signal),
  measurements: (id: string, signal?: AbortSignal) =>
    get<MeasurementsResponse>(`/api/files/${id}/measurements/?limit=1`, signal),
  measurement: (id: string, index: number, signal?: AbortSignal) =>
    get<MeasurementsResponse>(`/api/files/${id}/measurements/?limit=1&offset=${index}`, signal),
  feature: (id: string, index: number, signal?: AbortSignal) =>
    get<FeatureDetail>(`/api/files/${id}/features/${index}/`, signal),
  geojson: async (id: string, signal?: AbortSignal) => {
    const res = await fetch(`/api/files/${id}/geojson/`, { signal });
    if (!res.ok) throw await parseError(res);
    return {
      collection: (await res.json()) as MapCollection,
      truncated: res.headers.get("X-Truncated") === "true",
      total: Number(res.headers.get("X-Total-Count") ?? 0),
    };
  },
  geojsonUrl: (id: string) => `/api/files/${id}/geojson/`,
  deleteFile: async (id: string) => {
    const res = await fetch(`/api/files/${id}/`, { method: "DELETE" });
    if (!res.ok) throw await parseError(res);
  },

  /** Upload with progress events (fetch can't report upload progress, XHR can). */
  upload(file: File | Blob, filename: string, onProgress: (fraction: number) => void): Promise<GeoFile> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file, filename);
      xhr.open("POST", "/api/files/");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.upload.onload = () => onProgress(1);
      xhr.onerror = () => reject(new ApiError("Network error: is the API running?", 0, "network_error"));
      xhr.onload = () => {
        let body: any = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* not JSON */
        }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(body as GeoFile);
        const err = body?.error;
        reject(
          new ApiError(
            err?.message ?? `Upload failed (${xhr.status})`,
            xhr.status,
            err?.code ?? "http_error",
            err?.file_id,
          ),
        );
      };
      xhr.send(form);
    });
  },
};

// ------------------------------------------------------------------ terrain

export interface Raster {
  id: string;
  filename: string;
  size_bytes: number;
  status: FileStatus;
  error: string | null;
  crs: string | null;
  width: number | null;
  height: number | null;
  cell_size_m: [number, number] | null;
  bbox: [number, number, number, number] | null;
  z_min: number | null;
  z_max: number | null;
  z_mean: number | null;
  valid_pct: number | null;
  warnings: string[];
  processing_ms: number | null;
  created_at: string;
}

export type BaseMethod = "triangulated" | "plane" | "lowest" | "highest" | "mean" | "custom" | "surface";

export interface BaseSpec {
  method: BaseMethod;
  elevation?: number | null;
  raster_id?: string | null;
}

export interface FeatureRef {
  file_id: string;
  index: number;
}

export interface VolumeResult {
  raster_id: string;
  base_method: BaseMethod;
  base_elevation: { min: number; max: number; mean: number; boundary_relief_m?: number };
  cut_m3: number;
  fill_m3: number;
  net_m3: number;
  area_2d_m2: number;
  surface_area_3d_m2: number;
  elevation: { min: number; max: number; mean: number };
  max_height_above_base_m: number;
  max_depth_below_base_m: number;
  slope: { mean_deg: number; max_deg: number; threshold_deg: number; steeper_than_threshold_pct: number };
  cells: { inside: number; valid: number; nodata: number };
  coverage_pct: number;
  cell_size_m: [number, number];
  cell_method: string;
  plane: { dz_dx: number; dz_dy: number; z0: number } | null;
  warnings: string[];
}

export interface BatchVolumeItem {
  index: number;
  name: string | null;
  status: "OK" | "SKIPPED" | "FAILED";
  message: string | null;
  result: VolumeResult | null;
}

export interface BatchVolumes {
  raster_id: string;
  file_id: string;
  base_method: BaseMethod;
  totals: { cut_m3: number; fill_m3: number; net_m3: number; area_2d_m2: number; surface_area_3d_m2: number };
  measured: number;
  skipped: number;
  failed: number;
  items: BatchVolumeItem[];
}

export interface ProfileResult {
  raster_id: string;
  length_m: number;
  stats: {
    samples: number;
    nodata_samples: number;
    min_m: number;
    max_m: number;
    start_m: number;
    end_m: number;
    total_climb_m: number;
    total_descent_m: number;
    mean_abs_grade_pct: number;
    max_sustained_grade_pct: number;
    sample_spacing_m: number;
  };
  distance_m: number[];
  elevation_m: (number | null)[];
  lon: number[];
  lat: number[];
}

async function post<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}

export type TerrainTarget = { geometry: GeoJSON.Geometry } | { feature: FeatureRef };

export const terrain = {
  list: (signal?: AbortSignal) => get<Page<Raster>>(`/api/rasters/?limit=50`, signal),
  get: (id: string, signal?: AbortSignal) => get<Raster>(`/api/rasters/${id}/`, signal),
  remove: async (id: string) => {
    const res = await fetch(`/api/rasters/${id}/`, { method: "DELETE" });
    if (!res.ok) throw await parseError(res);
  },
  tileUrl: (id: string, style: string) => `/api/rasters/${id}/tiles/{z}/{x}/{y}.png?style=${style}`,
  downloadUrl: (id: string) => `/api/rasters/${id}/download/`,
  volume: (id: string, target: TerrainTarget, base: BaseSpec, signal?: AbortSignal) =>
    post<VolumeResult>(`/api/rasters/${id}/volume/`, { ...target, base }, signal),
  volumes: (id: string, fileId: string, base: BaseSpec, signal?: AbortSignal) =>
    post<BatchVolumes>(`/api/rasters/${id}/volumes/`, { file_id: fileId, base }, signal),
  profile: (id: string, target: TerrainTarget, signal?: AbortSignal) =>
    post<ProfileResult>(`/api/rasters/${id}/profile/`, { ...target, max_samples: 600 }, signal),
  altitude: (id: string, lon: number, lat: number, signal?: AbortSignal) =>
    get<{ elevation_m: number | null }>(`/api/rasters/${id}/altitude/?lon=${lon}&lat=${lat}`, signal),
};

/** Upload a file to a given endpoint with progress events. */
export function uploadTo<T>(
  url: string,
  file: File | Blob,
  filename: string,
  onProgress: (fraction: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file, filename);
    xhr.open("POST", url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.upload.onload = () => onProgress(1);
    xhr.onerror = () => reject(new ApiError("Network error: is the API running?", 0, "network_error"));
    xhr.onload = () => {
      let body: any = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T);
      const err = body?.error;
      reject(
        new ApiError(err?.message ?? `Upload failed (${xhr.status})`, xhr.status, err?.code ?? "http_error", err?.file_id),
      );
    };
    xhr.send(form);
  });
}

export const isRasterName = (name: string) => /\.(tif|tiff)$/i.test(name);
