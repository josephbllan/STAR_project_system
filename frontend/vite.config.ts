import { defineConfig, Plugin } from "vite";
import react from "@vitejs/plugin-react";

/** Vite treats `/index` as `index.html` and serves a blank document. Rewrite so the SPA loads. */
function serveIndexRoute(): Plugin {
  const rewrite = (req: { url?: string }) => {
    const path = req.url?.split("?")[0];
    if (path === "/index" || path === "/index/") {
      req.url = "/index.html";
    }
  };
  return {
    name: "serve-index-route",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        rewrite(req);
        next();
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use((req, _res, next) => {
        rewrite(req);
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [serveIndexRoute(), react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    headers: {
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "same-origin",
      "Content-Security-Policy":
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self' ws://127.0.0.1:5173",
    },
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/files": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
