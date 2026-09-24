import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `base: "./"` keeps every asset and data path relative, so the same build works
// at a domain root (Vercel) and under a repository subpath (GitHub Pages).
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  preview: { port: 4173, strictPort: true },
});
