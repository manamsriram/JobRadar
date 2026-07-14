import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths so the build works under the Pages subpath (/JobRadar/).
  base: "./",
  server: {
    // Dev proxy so /api (REST + SSE) hits the FastAPI backend.
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
