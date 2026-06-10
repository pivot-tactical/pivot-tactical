import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/// <reference types="vitest" />
// PIVOT frontend build. Output (dist/) is embedded into the executable via
// PyInstaller --add-data and served by FastAPI at the LAN address (spec §9.1).
// During development the dev server proxies /api and /ws to the Python server.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
});
