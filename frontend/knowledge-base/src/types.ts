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
export type Space = { space_scope_id: string; title: string; version: number };
export type DefaultSpace = { space_scope_id: string; version: number; available: boolean };
export type KB = {
  scope_id: string;
  space_scope_id: string;
  version: number;
  value: {
    title: string;
    summary: string;
    chunking: { max_chars: number; overlap: number };
    embedding_profile: string;
  };
};
export type Ref = { artifact_id: string; revision: number; scope_id: string };
export type Content = {
  title: string;
  relative_path: string;
  chunk_count: number;
  manually_modified: boolean;
  original_source: unknown;
};
export type Document = { ref: Ref; content: Content; lifecycle_state: string };
export type Head = { artifact_id: string; revision: number; content: Content };
export type Processing = {
  request_id: string;
  document_id: string;
  state: string;
  error?: string | null;
  current_revision?: number | null;
};
export type DocPage = { items: Document[]; next_cursor: string | null; requests: Processing[] };
export type Chunk = {
  chunk_id: string;
  ordinal: number;
  heading_path: string[];
  length: number;
  locator: Record<string, unknown>;
  manually_modified: boolean;
  text: string;
};
export type ChunkPage = { items: Chunk[]; total: number; next_offset: number | null };
export type Hit = {
  scope_id: string;
  document: Ref;
  chunk_id: string;
  text: string;
  heading_path: string[];
  locator: Record<string, unknown>;
  manually_modified: boolean;
  score: number;
};
export type Location = { scope: string; id: string; revision?: number; chunk?: string };
export type Target = { scope: string; id: string; revision: number };
export type Receipt = { path: string; accepted: boolean; request_id?: string; document_id?: string; error?: string };
export type Edit =
  | { operation: "edit_chunk" | "remove_chunk"; chunk_id: string; text?: string }
  | { operation: "remove_chunks"; chunk_ids: string[] };
