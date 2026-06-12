import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: true,
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
