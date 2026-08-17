import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: single-origin /api split between the two backends
// (asset-management :8000, data-factory :8001) — matches the production
// nginx gateway in infra/nginx (see infra/docs/contract.md).
const ASSET_API_PATHS = [
  "/api/assets",
  "/api/sources",
  "/api/snapshots",
  "/api/sync",
  "/api/downloads",
  "/api/cluster",
  "/api/info",
  "/api/backup",
];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      ...Object.fromEntries(
        ASSET_API_PATHS.map((path) => [
          path,
          {
            target: "http://localhost:8000",
            changeOrigin: true,
          },
        ]),
      ),
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
    },
  },
});
