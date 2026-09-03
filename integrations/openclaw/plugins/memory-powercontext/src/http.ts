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

export class PowerContextRequestError extends Error {
  readonly status?: number;
  readonly path: string;
  readonly code?: string;

  constructor(path: string, message: string, status?: number, code?: string) {
    super(message);
    this.name = "PowerContextRequestError";
    this.path = path;
    this.status = status;
    this.code = code;
  }
}

export type PowerContextClient = ReturnType<typeof createPowerContextClient>;

export function createPowerContextClient(getConfig: () => PowerContextConfig) {
  async function request<T>(
    method: "GET" | "POST",
    path: string,
    body?: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<T> {
    const config = getConfig();
    if (!config.endpoint) {
      throw new PowerContextRequestError(path, "PowerContext endpoint is not configured");
    }
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) {
      controller.abort();
    }
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, config.timeoutMs);
    try {
      const token = process.env[config.tokenEnv];
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (token) {
        headers.authorization = `Bearer ${token}`;
      }
      let response: Response;
      try {
        response = await fetch(`${config.endpoint}${path}`, {
          method,
          headers,
          ...(body ? { body: JSON.stringify(body) } : {}),
          signal: controller.signal,
        });
      } catch (error) {
        const reason = timedOut
          ? `request timed out after ${config.timeoutMs}ms`
          : signal?.aborted
            ? "request aborted"
            : String(error);
        throw new PowerContextRequestError(path, reason);
      }
      const raw = await response.text();
      let payload: unknown = {};
      if (raw.trim()) {
        try {
          payload = JSON.parse(raw);
        } catch {
          payload = { raw };
        }
      }
      if (!response.ok) {
        const record = typeof payload === "object" && payload !== null ? payload : undefined;
        const error =
          record && "error" in record && typeof record.error === "object" && record.error !== null
            ? record.error
            : undefined;
        const detail =
          error && "message" in error && typeof error.message === "string"
            ? error.message
            : record && "detail" in record && typeof record.detail === "string"
              ? record.detail
              : `HTTP ${response.status}`;
        const code =
          error && "code" in error && typeof error.code === "string"
            ? error.code
            : undefined;
        throw new PowerContextRequestError(path, detail, response.status, code);
      }
      return payload as T;
    } catch (error) {
      if (error instanceof PowerContextRequestError) {
        throw error;
      }
      throw new PowerContextRequestError(path, String(error));
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }

  return {
    get<T>(path: string, signal?: AbortSignal) {
      return request<T>("GET", path, undefined, signal);
    },
    post<T>(path: string, body: Record<string, unknown>, signal?: AbortSignal) {
      return request<T>("POST", path, body, signal);
    },
  };
}
