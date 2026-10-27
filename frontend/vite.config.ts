import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode }) => {
  const envDir = fileURLToPath(new URL("..", import.meta.url));
  const env = loadEnv(mode, envDir, "");
  const target = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";
  const url = new URL(target);
  if (
    url.protocol !== "http:" ||
    !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
  ) {
    throw new Error(
      "VITE_API_PROXY_TARGET must point to a local HTTP backend.",
    );
  }
  const proxy = {
    "/api": {
      target,
      rewrite: (path: string) => path.replace(/^\/api/, ""),
      timeout: 610_000,
      proxyTimeout: 610_000,
    },
  };
  return {
    plugins: [react()],
    envDir,
    server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
    preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
  };
});
