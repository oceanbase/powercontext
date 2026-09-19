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

import { invoke, isTauri } from "@tauri-apps/api/core";
import type {
  FoundationInfo,
  WriteOutcome,
  MemoryCitation,
  MemoryEntry,
  SearchMemoryResponse,
  DiagnosticKind,
  DiagnosticReport,
  DesktopState,
  ProfileInput,
  ScopePage,
  ScopeDescriptor,
} from "../generated/ipc";
export async function getFoundationInfo(): Promise<FoundationInfo | null> {
  if (!isTauri()) return null;
  return invoke<FoundationInfo>("foundation_info");
}

export const desktopApi = {
  remember: (generation: number, text: string) =>
    invoke<WriteOutcome>("remember_memory", { generation, text }),
  search: (generation: number, query: string) =>
    invoke<SearchMemoryResponse>("search_memory", { generation, query }),
  entry: (generation: number, citation: MemoryCitation) =>
    invoke<MemoryEntry>("memory_entry", { generation, citation }),
  cancelMemory: (generation: number) =>
    invoke<void>("cancel_memory_reads", { generation }),
  diagnostics: (kind: DiagnosticKind) =>
    invoke<DiagnosticReport>("local_diagnostics", { kind }),
  state: () => invoke<DesktopState>("desktop_state"),
  save: (input: ProfileInput) =>
    invoke<DesktopState>("save_profile", { input }),
  remove: (id: string, revision: number) =>
    invoke<DesktopState>("remove_profile", { id, revision }),
  check: (id: string, activate: boolean) =>
    invoke<DesktopState>("check_connection", { id, activate }),
  disconnect: () => invoke<DesktopState>("disconnect"),
  invalidate: (id: string) =>
    invoke<DesktopState>("invalidate_profile", { id }),
  scopes: (generation: number, query: string, cursor: string | null) =>
    invoke<ScopePage>("list_scopes", { generation, query, cursor }),
  cancelScopes: (generation: number) =>
    invoke<void>("cancel_scope_reads", { generation }),
  defaultScope: (generation: number) =>
    invoke<ScopeDescriptor>("default_scope", { generation }),
  selectScope: (generation: number, id: string) =>
    invoke<DesktopState>("select_scope", { generation, id }),
};
