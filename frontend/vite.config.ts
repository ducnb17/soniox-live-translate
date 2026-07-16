import { defineConfig } from "vite";
import { copyFileSync, existsSync, mkdirSync } from "fs";
import { resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: ".",
  base: "./",
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    // Copy setup.html (non-entry HTML) into dist/ so the backend can serve it.
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
  },
  plugins: [
    {
      name: "copy-setup-html",
      closeBundle() {
        const src = resolve(__dirname, "setup.html");
        if (existsSync(src)) {
          const outDir = resolve(__dirname, "dist");
          if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
          copyFileSync(src, resolve(outDir, "setup.html"));
        }
        // Also copy styles.css if it's referenced by setup.html via /styles.css
        const cssSrc = resolve(__dirname, "styles.css");
        if (existsSync(cssSrc)) {
          const outDir = resolve(__dirname, "dist");
          copyFileSync(cssSrc, resolve(outDir, "styles.css"));
        }
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:8765",
      "/config": "http://127.0.0.1:8765",
      "/setup": "http://127.0.0.1:8765",
      "/transcript": "http://127.0.0.1:8765",
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true,
      },
    },
  },
});
