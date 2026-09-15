import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const currentDirectory = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: resolve(currentDirectory, "../app/src/main/assets"),
  cacheDir: resolve(currentDirectory, "node_modules/.vite"),
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
});
