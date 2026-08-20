import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "POWERCONTEXT_EVAL_");
  const proxyTarget = environment.POWERCONTEXT_EVAL_DEV_PROXY_TARGET;

  return {
    plugins: [react()],
    build: {
      outDir: "dist",
    },
    ...(proxyTarget
      ? {
          server: {
          proxy: {
            "/api": {
              target: proxyTarget,
              changeOrigin: false,
            },
          },
          },
        }
      : {}),
    test: {
      css: true,
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
    },
  };
});
