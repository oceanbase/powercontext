/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

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
