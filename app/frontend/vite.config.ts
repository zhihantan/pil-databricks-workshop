import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Build output goes to the backend's static dir so FastAPI serves the SPA.
// Dev server proxies /api to the local backend on :8000.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
