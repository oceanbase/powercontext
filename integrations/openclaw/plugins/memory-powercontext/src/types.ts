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


export type ArtifactReference = {
  family: string;
  artifact_id: string;
  revision: number;
};

export type MemoryCitation = {
  memory_ref: ArtifactReference;
  entry_id: string;
  entry_version_id: string;
};

export type SearchMemoryHit = {
  citation: MemoryCitation;
  text: string;
  score: number;
  matched_by: Array<"fts" | "vector">;
};

export type SearchMemoryResponse = {
  memory: ArtifactReference | null;
  mode: "fts" | "vector" | "hybrid" | null;
  hits: SearchMemoryHit[];
};

export type MemoryEntry = {
  citation: MemoryCitation;
  version: number;
  kind: string;
  text: string;
  state: "active" | "inactive";
};

export type { PreparedContext } from "./client.js";

export type PowerContextCapabilities = {
  memory_extraction: boolean;
};

export function isPowerContextCapabilities(value: unknown): value is PowerContextCapabilities {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    typeof (value as Partial<PowerContextCapabilities>).memory_extraction === "boolean"
  );
}

export type MemoryMutationResponse = {
  memory: ArtifactReference;
  entry: MemoryEntry | null;
};

export function encodeCitation(citation: MemoryCitation): string {
  return `powercontext:${Buffer.from(JSON.stringify(citation), "utf8").toString("base64url")}`;
}

export function decodeCitation(value: string): MemoryCitation {
  const normalized = value.trim();
  if (!normalized.startsWith("powercontext:") || normalized.length > 4096) {
    throw new Error("citation must be the exact powercontext citation returned by memory_search");
  }
  const encoded = normalized.slice("powercontext:".length);
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
  } catch {
    throw new Error("citation must be the exact powercontext citation returned by memory_search");
  }
  if (!isMemoryCitation(parsed)) {
    throw new Error("citation is not a valid PowerContext MemoryCitation");
  }
  return parsed;
}

export function isMemoryCitation(value: unknown): value is MemoryCitation {
  if (!value || typeof value !== "object") {
    return false;
  }
  const citation = value as Partial<MemoryCitation>;
  const memory = citation.memory_ref;
  return (
    typeof citation.entry_id === "string" && citation.entry_id.length > 0 &&
    typeof citation.entry_version_id === "string" && citation.entry_version_id.length > 0 &&
    Boolean(memory) &&
    typeof memory?.family === "string" && memory.family.length > 0 &&
    typeof memory.artifact_id === "string" && memory.artifact_id.length > 0 &&
    Number.isInteger(memory.revision) && memory.revision >= 1
  );
}
