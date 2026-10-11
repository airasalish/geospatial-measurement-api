import { useRef, useState } from "react";
import type { ApiError, GeoFile } from "../api";
import { formatBytes, timeAgo } from "../units";

export const SAMPLES = [
  { file: "site_survey.kml", title: "Site survey", detail: "KML · mixed geometry" },
  { file: "farm_plots.zip", title: "Farm plots", detail: "Shapefile · 400+ parcels" },
  { file: "parcels_utm43n.zip", title: "Parcels", detail: "Shapefile · UTM 43N" },
] as const;

export interface UploadState {
  name: string;
  progress: number; // 0..1 of bytes sent
  phase: "uploading" | "processing";
}

export function Logo({ size = 34 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#1c1f1d" />
      <circle cx="16" cy="16" r="8.5" fill="none" stroke="#e4532a" strokeWidth="2.4" />
      <path d="M16 3v8M16 21v8M3 16h8M21 16h8" stroke="#f3eee3" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="16" cy="16" r="2.2" fill="#f3eee3" />
    </svg>
  );
}

interface UploadProps {
  upload: UploadState | null;
  error: ApiError | null;
  onFile: (file: File) => void;
  onSample: (file: string) => void;
  onOpenFailed: (id: string) => void;
  onDismissError: () => void;
}

export function UploadZone({ upload, error, onFile, onSample, onOpenFailed, onDismissError }: UploadProps) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const busy = upload !== null;

  return (
    <>
      <button
        type="button"
        className={`dropzone${over ? " is-over" : ""}`}
        onClick={() => !busy && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const file = e.dataTransfer.files[0];
          if (file && !busy) onFile(file);
        }}
        aria-busy={busy}
        aria-label="Upload a geospatial file"
      >
        {upload ? (
          <div className="upload-progress" aria-live="polite">
            <div className="row">
              <span className="name">{upload.name}</span>
              <span className="mono">
                {upload.phase === "uploading" ? `${Math.round(upload.progress * 100)}%` : "measuring…"}
              </span>
            </div>
            <div className={`bar${upload.phase === "processing" ? " is-indeterminate" : ""}`}>
              <span style={{ width: `${upload.progress * 100}%` }} />
            </div>
            <span className="dropzone-hint">
              {upload.phase === "uploading"
                ? "Uploading"
                : "Parsing features, reprojecting, measuring on the ellipsoid"}
            </span>
          </div>
        ) : (
          <>
            <UploadIcon />
            <span className="dropzone-title">Drop a file or click to browse</span>
            <span className="dropzone-hint">
              <kbd>.zip</kbd> Shapefile · <kbd>.kml</kbd> · <kbd>.kmz</kbd> — up to 50 MB
            </span>
          </>
        )}
      </button>
      <input
        ref={input}
        type="file"
        accept=".zip,.kml,.kmz"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
      {error && (
        <div className="alert" role="alert">
          <strong>{error.code.replace(/_/g, " ")}</strong>
          {error.message}
          <div>
            {error.fileId && (
              <button type="button" onClick={() => onOpenFailed(error.fileId!)}>
                View failed record
              </button>
            )}{" "}
            <button type="button" onClick={onDismissError}>
              Dismiss
            </button>
          </div>
        </div>
      )}
      <div className="samples" aria-label="Sample files">
        {SAMPLES.map((s) => (
          <button
            key={s.file}
            type="button"
            className="btn btn-sm"
            disabled={busy}
            onClick={() => onSample(s.file)}
            title={s.detail}
          >
            {s.title}
          </button>
        ))}
      </div>
    </>
  );
}

function UploadIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M12 15V4m0 0L8 8m4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" strokeLinecap="round" />
    </svg>
  );
}

interface FileListProps {
  files: GeoFile[];
  activeId: string | null;
  loading: boolean;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}

export function FileList({ files, activeId, loading, onOpen, onDelete }: FileListProps) {
  const [confirming, setConfirming] = useState<string | null>(null);

  if (!files.length) {
    return <p className="empty-note">{loading ? "Loading…" : "No files yet. Upload one or try a sample."}</p>;
  }
  return (
    <ul className="file-list">
      {files.map((f) => (
        <li key={f.id} style={{ position: "relative" }}>
          <button
            type="button"
            className={`file-item${f.id === activeId ? " is-active" : ""}`}
            onClick={() => onOpen(f.id)}
            aria-current={f.id === activeId}
          >
            <span className="fmt">{f.format === "shapefile" ? "SHP" : f.format.toUpperCase()}</span>
            <span className="fname" title={f.filename}>
              {f.filename}
            </span>
            <span className="fmeta">
              <span className={`dot ${f.status}`} />{" "}
              {f.status === "FAILED"
                ? "failed"
                : `${f.feature_count.toLocaleString()} feature${f.feature_count === 1 ? "" : "s"}`}{" "}
              · {formatBytes(f.size_bytes)} · {timeAgo(f.created_at)}
            </span>
          </button>
          <div
            className={`factions${confirming === f.id ? " is-confirming" : ""}`}
            style={{ position: "absolute", right: 14, top: "50%", transform: "translateY(-50%)" }}
          >
            {confirming === f.id ? (
              <span className="confirm">
                <button type="button" className="btn btn-sm btn-flag" onClick={() => onDelete(f.id)}>
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
                aria-label={`Delete ${f.filename}`}
                title="Delete"
                onClick={() => setConfirming(f.id)}
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
