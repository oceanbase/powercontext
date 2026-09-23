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


import type { PowerContextConfig } from "./config.js";
import { PowerContextClient as SharedClient, type JsonObject } from "./client.js";
import { OPERATIONS } from "./operations.generated.js";
import { InvalidResponseError, ServerResponseError, UnknownOutcomeError } from "./errors.js";

export class PowerContextRequestError extends Error {
  constructor(
    readonly path: string,
    message: string,
    readonly status?: number,
    readonly code?: string,
    readonly outcome?: "unknown",
  ) { super(message); this.name = "PowerContextRequestError"; }
}

export type PowerContextClient = ReturnType<typeof createPowerContextClient>;

function operationForPath(method: string, path: string): { id: string; arguments: JsonObject } {
  const url = new URL(path, "http://powercontext.local");
  const parts = url.pathname.split("/");
  for (const [id, spec] of Object.entries(OPERATIONS)) {
    const pattern = spec.path.split("/");
    if (spec.method !== method || pattern.length !== parts.length) continue;
    const arguments_: JsonObject = Object.fromEntries(url.searchParams);
    const matches = pattern.every((part, index) => {
      if (part.startsWith("{") && part.endsWith("}")) {
        arguments_[part.slice(1, -1)] = decodeURIComponent(parts[index]!);
        return true;
      }
      return part === parts[index];
    });
    if (matches) return { id, arguments: arguments_ };
  }
  throw new PowerContextRequestError(path, "Unknown PowerContext operation");
}

export function createPowerContextClient(getConfig: () => PowerContextConfig) {
  let client: SharedClient | undefined;
  let connection: string | undefined;
  async function request<T>(method: string, path: string, body?: JsonObject, signal?: AbortSignal): Promise<T> {
    const config = getConfig();
    if (!config.endpoint) throw new PowerContextRequestError(path, "PowerContext endpoint is not configured");
    const token = process.env[config.tokenEnv];
    const settings = {
      baseUrl: config.endpoint,
      allowInsecureHttp: config.allowInsecureHttp,
      authorization: token ? `Bearer ${token}` : undefined,
      requestTimeoutMs: config.timeoutMs,
    };
    try {
      const key = JSON.stringify(settings);
      if (!client || key !== connection) {
        client?.close();
        client = new SharedClient(settings);
        connection = key;
      }
      const operation = operationForPath(method, path);
      const result = await client.request(operation.id, { ...operation.arguments, ...body }, signal);
      return result.value as T;
    } catch (error) {
      if (error instanceof PowerContextRequestError) throw error;
      const status = error instanceof ServerResponseError || error instanceof InvalidResponseError ? error.statusCode : undefined;
      const code = error instanceof ServerResponseError && typeof error.code === "string" ? error.code : undefined;
      throw new PowerContextRequestError(path, error instanceof Error ? error.message : "PowerContext request failed",
        status, code, error instanceof UnknownOutcomeError || (error as { outcome?: string })?.outcome === "unknown" ? "unknown" : undefined);
    }
  }
  return {
    get<T>(path: string, signal?: AbortSignal) { return request<T>("GET", path, undefined, signal); },
    post<T>(path: string, body: JsonObject, signal?: AbortSignal) { return request<T>("POST", path, body, signal); },
    close() { client?.close(); },
  };
}
