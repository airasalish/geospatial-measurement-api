import { useRef, useState } from "react";
import type { ApiError, GeoFile } from "../api";
import { formatBytes, timeAgo } from "../units";

/** Files in /samples, served by the API. Open = upload now; download = try the upload flow yourself. */
export const SAMPLES = [
  {
    file: "site_survey.kml",
    format: "KML",
    title: "Site survey",
    detail: "5 features · 1.4 KB",
  },
  {
    file: "farm_plots.zip",
    format: "SHP",
    title: "Farm plots, Devanahalli",
    detail: "442 features · 3 layers",
  },
  {
    file: "parcels_utm43n.zip",
    format: "SHP",
    title: "Parcels in UTM 43N",
    detail: "Projected CRS · 3 features",
  },
] as const;

export interface UploadState {
  name: string;
  progress: number; // 0..1 of bytes sent
  phase: "uploading" | "processing";
}

/**
 * The Traverse mark: a closed survey traverse. Five stations joined by measured
 * legs, the start station highlighted where the loop closes.
 */
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 36 36" aria-hidden="true">
      <rect width="36" height="36" rx="8" fill="#1c1f1d" />
      <path d="M8 24 11.5 10 24 7.5 28.5 19.5 18 28Z" fill="rgba(228,83,42,.16)" stroke="#e4532a" strokeWidth="2" strokeLinejoin="round" />
      <g fill="#f3eee3">
        <circle cx="11.5" cy="10" r="1.9" />
        <circle cx="24" cy="7.5" r="1.9" />
        <circle cx="28.5" cy="19.5" r="1.9" />
        <circle cx="18" cy="28" r="1.9" />
      </g>
      <circle cx="8" cy="24" r="3" fill="#e4532a" stroke="#1c1f1d" strokeWidth="1.4" />
    </svg>
  );
}

interface UploadProps {
  upload: UploadState | null;
  error: ApiError | null;
  onFile: (file: File) => void;
  onOpenFailed: (id: string) => void;
  onDismissError: () => void;
}

export function UploadZone({ upload, error, onFile, onOpenFailed, onDismissError }: UploadProps) {
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
    </>
  );
}

export function SampleList({ busy, onOpen }: { busy: boolean; onOpen: (file: string) => void }) {
  return (
    <ul className="sample-list">
      {SAMPLES.map((s) => (
        <li key={s.file} className="sample">
          <span className="fmt">{s.format}</span>
          <span className="sname">{s.title}</span>
          <span className="smeta">{s.detail}</span>
          <span className="sactions">
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => onOpen(s.file)}
              title="Upload and measure this file now"
            >
              Open
            </button>
            <a
              className="icon-btn"
              href={`/samples/${s.file}`}
              download={s.file}
              aria-label={`Download ${s.file}`}
              title={`Download ${s.file}`}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          </span>
        </li>
      ))}
    </ul>
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
