import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";

import { brandIcons } from "./brandIcons";

// This file's folder (frontend/), wherever Vite is started from.
const here = fileURLToPath(new URL(".", import.meta.url));

// In development the UI runs on Vite and the API on DataLab. Cookies are
// per host, not per port, so signing in through DataLab works for both.
const backend = process.env.DATALAB_URL ?? "http://127.0.0.1:8766";

export default defineConfig({
  plugins: [react(), tailwindcss(), brandIcons()],
  resolve: { alias: { "@": "/src" } },
  server: {
    host: "127.0.0.1",
    port: 5173,
    // The user guide (docs/guide) is bundled from outside the frontend folder.
    fs: { allow: [searchForWorkspaceRoot(here), fileURLToPath(new URL("../docs/guide", import.meta.url))] },
    proxy: { "/api": backend, "/sign-in": backend },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
  },
});
