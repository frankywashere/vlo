import { resolve } from "node:path";
import { readFileSync, readdirSync } from "node:fs";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// A public site build deliberately excludes ignored desktop demo media and VLO's editor.
const localLibraryApi =
  process.env.RECASTFX_LOCAL_LIBRARY_API ||
  loadEnv("development", import.meta.dirname, "RECASTFX_")
    .RECASTFX_LOCAL_LIBRARY_API;
const apiProxy = {
  ...(localLibraryApi
    ? {
        // Optional local test media; account writes still use the hosted gateway.
        "^/api/(?:account/jobs(?:\\?|$)|local-library/media/)": {
          target: localLibraryApi,
          changeOrigin: true,
          headers: { Origin: "https://recastfx.com" },
        },
      }
    : {}),
  ...(process.env.RECASTFX_TEMPLATE_API ||
  loadEnv("development", import.meta.dirname, "RECASTFX_").RECASTFX_TEMPLATE_API
    ? {
        "^/api/(templates|commerce/admin/templates)(?:/|\\?|$)": {
          target:
            process.env.RECASTFX_TEMPLATE_API ||
            loadEnv("development", import.meta.dirname, "RECASTFX_")
              .RECASTFX_TEMPLATE_API,
          changeOrigin: true,
          headers: { Origin: "https://recastfx.com" },
        },
      }
    : {}),
  "/api": {
    target: "https://recastfx.com",
    changeOrigin: true,
    // The hosted gateway validates its website origin as well as Firebase tokens.
    headers: { Origin: "https://recastfx.com" },
  },
};
export default defineConfig({
  plugins: [
    react(),
    {
      name: "recast-pwa-assets",
      generateBundle() {
        const root = resolve(import.meta.dirname, "pwa");
        for (const fileName of readdirSync(root))
          this.emitFile({
            type: "asset",
            fileName,
            source: readFileSync(resolve(root, fileName)),
          });
      },
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const name = req.url?.split("?")[0]?.slice(1);
          if (
            !name ||
            ![
              "manifest.webmanifest",
              "sw.js",
              "offline.html",
              "icon-192.png",
              "icon-512.png",
            ].includes(name)
          )
            return next();
          res.setHeader(
            "Content-Type",
            name.endsWith(".js")
              ? "text/javascript"
              : name.endsWith(".png")
                ? "image/png"
                : name.endsWith(".html")
                  ? "text/html"
                  : "application/manifest+json",
          );
          res.end(readFileSync(resolve(import.meta.dirname, "pwa", name)));
        });
      },
    },
  ],
  publicDir: "public/charSwap-runtime",
  worker: { format: "es" },
  // Account metadata stays useful in local previews with Firebase sign-in.
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
  build: {
    outDir: "dist-swapmein",
    rollupOptions: { input: resolve(import.meta.dirname, "charswap.html") },
  },
});
