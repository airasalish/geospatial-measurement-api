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
