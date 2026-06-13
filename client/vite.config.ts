import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    // Public source maps shipped 1.6 MB of internal code paths to every
    // visitor — useful for local debugging, not for prod. "hidden" still
    // generates the .map files (Sentry-style uploads possible later) but
    // omits the //# sourceMappingURL pointer so browsers don't fetch them.
    sourcemap: "hidden",
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:5001",
      "/sync": "http://localhost:5001",
      "/link": "http://localhost:5001",
      "/transactions": "http://localhost:5001",
      "/rules": "http://localhost:5001",
      "/planning": "http://localhost:5001",
      "/budget": "http://localhost:5001",
    },
  },
});
