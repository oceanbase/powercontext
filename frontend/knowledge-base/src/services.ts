/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
import type {
  Processing,
  Space,
  DefaultSpace,
  KB,
  DocPage,
  Head,
  Chunk,
  ChunkPage,
  Hit,
  Target,
  Receipt,
  Edit,
} from "./types";
type Options = {
  query?: Record<string, unknown>;
  json?: unknown;
  body?: ArrayBuffer;
  contentType?: string;
  scopeId?: string;
  ifMatch?: string;
};
type Response = { ok: boolean; status: number; json: any };
declare global {
  interface Window {
    powercontextPluginClient(): {
      context(): Promise<{ scopeId: string | null }>;
      request(method: string, path: string, options?: Options): Promise<Response>;
      artifacts(method: string, path: string, options?: Options): Promise<Response>;
    };
  }
}
const bridge = window.powercontextPluginClient();
async function result<T>(request: Promise<Response>): Promise<T> {
  const response = await request;
  if (!response.ok) throw new Error(`${response.status}：${response.json?.error?.message || "请求失败，请刷新后重试"}`);
  return response.json as T;
}
const api = <T>(method: string, path: string, options?: Options) => result<T>(bridge.request(method, path, options));
const artifact = <T>(scope: string, id: string, suffix = "", options: Options = {}, method = "GET") =>
  result<T>(bridge.artifacts(method, `/${id}${suffix}`, { ...options, scopeId: scope }));
const chunks = (scope: string, id: string, revision: number) =>
  `/scopes/${scope}/documents/${id}/revisions/${revision}/chunks`;
export const service = {
  context: () => bridge.context(),
  library: async (scope: string) => {
    const response = await bridge.request("GET", `/scopes/${encodeURIComponent(scope)}/settings`);
    if (response.status === 404 || response.status === 403) return null;
    return result<KB>(Promise.resolve(response));
  },
  spaces: () => api<{ items: Space[] }>("GET", "/spaces"),
  defaultSpace: () => api<DefaultSpace>("GET", "/me/default-space"),
  libraries: (space: string) => api<{ items: KB[] }>("GET", "/knowledge-bases", { query: { space_scope_id: space } }),
  documents: (scope: string, cursor: string | null) =>
    api<DocPage>("GET", `/scopes/${scope}/documents`, { query: { limit: 50, ...(cursor ? { cursor } : {}) } }),
  status: (scope: string, request: string) => api<Processing>("GET", `/scopes/${scope}/imports/${request}`),
  head: (scope: string, id: string) => artifact<Head>(scope, id),
  governance: (scope: string, id: string) =>
    artifact<{ lifecycle_state: string; state_version: number }>(scope, id, "/governance"),
  revisions: (scope: string, id: string, cursor: string | null) =>
    artifact<{ items: { revision: number }[]; next_cursor: string | null }>(scope, id, "/revisions", {
      query: { limit: 50, ...(cursor ? { cursor } : {}) },
    }),
  chunks: (scope: string, id: string, revision: number, offset: number) =>
    api<ChunkPage>("GET", chunks(scope, id, revision), { query: { offset, limit: 50 } }),
  chunk: (scope: string, id: string, revision: number, chunk: string) =>
    api<Chunk>("GET", `${chunks(scope, id, revision)}/${chunk}`),
  create: (kind: "space" | "kb", body: unknown) =>
    api<KB | Space>("POST", kind === "space" ? "/spaces" : "/knowledge-bases", { json: body }),
  setDefault: (space: string, version: number) =>
    api<DefaultSpace>("PUT", "/me/default-space", { json: { space_scope_id: space, version } }),
  settings: (kb: KB, chunking: KB["value"]["chunking"]) =>
    api<KB>("PATCH", `/scopes/${kb.scope_id}/settings`, { json: { version: kb.version, chunking } }),
  upload: async (scope: string, file: File, key: string) =>
    api<Receipt>("POST", `/scopes/${scope}/uploads`, {
      query: { relative_path: file.webkitRelativePath || file.name, idempotency_key: key },
      body: await file.arrayBuffer(),
      contentType: "application/octet-stream",
    }),
  imports: (scope: string, body: { root: string; paths: string[]; idempotency_key: string }) =>
    api<{ items: Receipt[] }>("POST", `/scopes/${scope}/imports`, { json: body }),
  edit: (target: Target, edit: Edit) =>
    artifact<Head>(
      target.scope,
      target.id,
      "",
      { json: { content: edit }, ifMatch: `"revision:${target.revision}"` },
      "PUT",
    ),
  forget: async (target: Target) => {
    const state = await service.governance(target.scope, target.id);
    return artifact(
      target.scope,
      target.id,
      "/forget",
      { json: { revision: target.revision, state_version: state.state_version } },
      "POST",
    );
  },
  reprocess: (target: Target, replace: boolean, key: string) =>
    api("POST", `/scopes/${target.scope}/documents/${target.id}/reprocess`, {
      json: {
        idempotency_key: key,
        expected_revision: target.revision,
        manual_edits_policy: replace ? "replace" : "reject",
      },
    }),
  retry: (scope: string, request: string, key: string, replace: boolean) =>
    api("POST", `/scopes/${scope}/imports/${request}/retry`, {
      json: { idempotency_key: key, manual_edits_policy: replace ? "replace" : "reject" },
    }),
  retrieve: (space: string, scopes: string[], query: string, mode: string) =>
    api<{ hits: Hit[] }>("POST", "/retrieve", {
      json: { space_scope_id: space, scope_ids: scopes, query, mode, limit: 10 },
    }),
};
