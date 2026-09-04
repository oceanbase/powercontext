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


import { createHash } from "node:crypto";
import type { OpenClawConfig } from "openclaw/plugin-sdk/plugin-entry";
import { asOptionalRecord } from "openclaw/plugin-sdk/string-coerce-runtime";

export type PowerContextConfig = {
  endpoint?: string;
  scopeId?: string;
  tokenEnv: string;
  timeoutMs: number;
  prepareMaxBytes: number;
  autoRecall: boolean;
  autoCapture: boolean;
  captureMaxChars: number;
};

const DEFAULT_CONFIG: PowerContextConfig = {
  tokenEnv: "POWERCONTEXT_CLIENT_API_TOKEN",
  timeoutMs: 2500,
  prepareMaxBytes: 8000,
  autoRecall: true,
  autoCapture: true,
  captureMaxChars: 4000,
};

function readPluginConfig(config: OpenClawConfig | undefined, fallback: unknown): Record<string, unknown> {
  const plugins = asOptionalRecord(config?.plugins);
  const entries = asOptionalRecord(plugins?.entries);
  const entry = asOptionalRecord(entries?.["memory-powercontext"]);
  return asOptionalRecord(entry?.config) ?? asOptionalRecord(fallback) ?? {};
}

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  return typeof value === "number" && Number.isInteger(value)
    ? Math.min(max, Math.max(min, value))
    : fallback;
}

function normalizeEndpoint(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const endpoint = value.trim().replace(/\/+$/u, "");
  if (!/^https?:\/\//iu.test(endpoint)) {
    return undefined;
  }
  try {
    const parsed = new URL(endpoint);
    return parsed.username || parsed.password ? undefined : endpoint;
  } catch {
    return undefined;
  }
}

export function resolvePowerContextConfig(
  config: OpenClawConfig | undefined,
  fallback?: unknown,
): PowerContextConfig {
  const raw = readPluginConfig(config, fallback);
  const endpoint = normalizeEndpoint(raw.endpoint);
  const tokenEnv =
    typeof raw.tokenEnv === "string" && /^[A-Za-z_][A-Za-z0-9_]*$/u.test(raw.tokenEnv.trim())
      ? raw.tokenEnv.trim()
      : DEFAULT_CONFIG.tokenEnv;
  const scopeId = typeof raw.scopeId === "string" && raw.scopeId.trim() ? raw.scopeId.trim() : undefined;
  return {
    ...DEFAULT_CONFIG,
    ...(endpoint ? { endpoint } : {}),
    ...(scopeId ? { scopeId } : {}),
    tokenEnv,
    timeoutMs: boundedInteger(raw.timeoutMs, DEFAULT_CONFIG.timeoutMs, 250, 15000),
    prepareMaxBytes: boundedInteger(raw.prepareMaxBytes, DEFAULT_CONFIG.prepareMaxBytes, 512, 32768),
    autoRecall: raw.autoRecall !== false,
    autoCapture: raw.autoCapture !== false,
    captureMaxChars: boundedInteger(raw.captureMaxChars, DEFAULT_CONFIG.captureMaxChars, 128, 20000),
  };
}

export function opaqueSessionId(sessionId: string | undefined, sessionKey: string | undefined): string | undefined {
  const value = (sessionId ?? sessionKey ?? "").trim();
  return value ? createHash("sha256").update(value).digest("hex") : undefined;
}
