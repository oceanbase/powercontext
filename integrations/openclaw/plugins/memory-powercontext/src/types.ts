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

export type AtomicMemoryInput = {
  artifact: ArtifactReference;
  state_version: number;
};

export type MemoryReference = MemoryCitation | AtomicMemoryInput | ArtifactReference;

export type AtomicMemoryRecord = {
  artifact: ArtifactReference;
  kind: string;
  text: string;
  state: "active" | "forgotten" | "merged" | "retired";
  state_version: number;
  merged_into_id: string | null;
};

export type MemoryMatchedBy = "text" | "vector";

export function isMemoryMatchedBy(value: unknown): value is MemoryMatchedBy {
  return value === "text" || value === "vector";
}

export type SearchMemoryHit = {
  memory: AtomicMemoryRecord;
  score: number;
  matched_by: MemoryMatchedBy[];
};

export type SearchMemoryResponse = {
  mode: "fts" | "vector" | "hybrid";
  hits: SearchMemoryHit[];
};

export type MemoryEntry = {
  citation: MemoryCitation;
  version: number;
  kind: string;
  text: string;
  state: "active" | "inactive";
};

export type ArtifactRevision = ArtifactReference & {
  scope_id: string;
  content: { kind: string; text: string; [key: string]: unknown };
};

export type PreparedContext = {
  schema: "powercontext.prepared-context.v1";
  status: "ready" | "empty";
  content: string | null;
  content_bytes: number;
};

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

export function isPreparedContext(value: unknown, maxBytes?: number): value is PreparedContext {
  if (!value || typeof value !== "object") {
    return false;
  }
  const prepared = value as Partial<PreparedContext>;
  if (maxBytes !== undefined) {
    const keys = Object.keys(value).sort().join(",");
    if (keys !== "content,content_bytes,schema,status") return false;
    if (prepared.status === "empty") {
      if (prepared.content !== null || prepared.content_bytes !== 0) return false;
    } else {
      if (typeof prepared.content !== "string" || !prepared.content) return false;
      const actualBytes = Buffer.byteLength(prepared.content, "utf8");
      if (actualBytes !== prepared.content_bytes || actualBytes > maxBytes) return false;
    }
  }
  return (
    prepared.schema === "powercontext.prepared-context.v1" &&
    (prepared.status === "ready" || prepared.status === "empty") &&
    (prepared.content === null || typeof prepared.content === "string") &&
    Number.isInteger(prepared.content_bytes) &&
    (prepared.content_bytes ?? -1) >= 0
  );
}

export type MemoryMutationResponse = {
  changed: boolean;
  records: AtomicMemoryRecord[];
};

export function encodeCitation(citation: MemoryReference): string {
  return `powercontext:${Buffer.from(JSON.stringify(citation), "utf8").toString("base64url")}`;
}

export function decodeCitation(value: string): MemoryReference {
  const normalized = value.trim();
  if (!normalized.startsWith("powercontext:") || normalized.length > 4096) {
    throw new Error("citation must be the exact powercontext citation/reference returned by memory_search");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(normalized.slice("powercontext:".length), "base64url").toString("utf8"));
  } catch {
    throw new Error("citation must be the exact powercontext citation/reference returned by memory_search");
  }
  if (!isMemoryCitation(parsed) && !isAtomicMemoryInput(parsed) && !isAtomicMemoryRef(parsed)) {
    throw new Error("citation is not a valid exact PowerContext memory reference");
  }
  return parsed;
}

export function isAtomicMemoryRef(value: unknown): value is ArtifactReference {
  if (!value || typeof value !== "object") return false;
  const ref = value as Partial<ArtifactReference>;
  return ref.family === "atomic-memory" && typeof ref.artifact_id === "string" && ref.artifact_id.length > 0 &&
    Number.isInteger(ref.revision) && (ref.revision ?? 0) > 0;
}

export function isAtomicMemoryInput(value: unknown): value is AtomicMemoryInput {
  if (!value || typeof value !== "object") return false;
  const input = value as Partial<AtomicMemoryInput>;
  return isAtomicMemoryRef(input.artifact) && Number.isInteger(input.state_version) && (input.state_version ?? -1) >= 0;
}

export function isAtomicMemoryRecord(value: unknown): value is AtomicMemoryRecord {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<AtomicMemoryRecord>;
  return isAtomicMemoryInput(value) && typeof record.kind === "string" && typeof record.text === "string" &&
    ["active", "forgotten", "merged", "retired"].includes(record.state ?? "");
}

export function atomicMemoryInput(record: AtomicMemoryRecord): AtomicMemoryInput {
  return { artifact: record.artifact, state_version: record.state_version };
}

/** Normalize fused RRF rank to its reachable channel bound; this is not confidence. */
export function normalizeMemoryScore(hit: SearchMemoryHit): number {
  const channels = Math.max(1, new Set(hit.matched_by).size);
  return Math.max(0, Math.min(1, hit.score / (channels / 61)));
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
    memory?.family === "memory" &&
    typeof memory.artifact_id === "string" && memory.artifact_id.length > 0 &&
    Number.isInteger(memory.revision) && memory.revision >= 1
  );
}
