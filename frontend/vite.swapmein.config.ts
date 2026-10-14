import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// A public site build deliberately excludes ignored desktop demo media and VLO's editor.
export default defineConfig({
  plugins: [react()],
  publicDir: false,
  build: {
    outDir: "dist-swapmein",
    rollupOptions: { input: resolve(import.meta.dirname, "charswap.html") },
  },
});
