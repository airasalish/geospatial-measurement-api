import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on :5173 and proxies API/sample requests to FastAPI on :8000.
// In production FastAPI serves the built files itself, so everything is same-origin.
const backend = process.env.API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": backend,
      "/samples": backend,
      "/health": backend,
    },
  },
  build: {
    chunkSizeWarningLimit: 1200, // maplibre-gl is ~800 kB on its own
    rollupOptions: {
      // Separate chunk: the map engine changes rarely, so browsers keep it cached across app releases.
      output: { manualChunks: { maplibre: ["maplibre-gl"] } },
    },
  },
});
