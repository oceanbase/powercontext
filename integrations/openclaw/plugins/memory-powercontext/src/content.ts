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


import { createHash, type Hash } from "node:crypto";
import { asOptionalRecord } from "openclaw/plugin-sdk/string-coerce-runtime";

function textBlocks(message: unknown): { role: string; text: string } | undefined {
  const record = asOptionalRecord(message);
  if (!record) {
    return undefined;
  }
  const role = record.role;
  if (role !== "user" && role !== "assistant") {
    return undefined;
  }
  if (typeof record.content === "string") {
    const text = record.content.trim();
    return text ? { role, text } : undefined;
  }
  if (!Array.isArray(record.content)) {
    return undefined;
  }
  const text = record.content
    .flatMap((block) => {
      const value = asOptionalRecord(block);
      return value?.type === "text" && typeof value.text === "string" ? [value.text] : [];
    })
    .join("\n")
    .trim();
  return text ? { role, text } : undefined;
}

export function latestUserText(messages: unknown[], fallback?: string): string | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const part = textBlocks(messages[index]);
    if (part?.role === "user") {
      return part.text;
    }
  }
  const value = fallback?.trim();
  return value || undefined;
}

export function captureTranscript(messages: unknown[], maxChars: number): string | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const part = textBlocks(messages[index]);
    if (!part || part.role !== "user") {
      continue;
    }
    const value = `User: ${part.text}`;
    return value.length > maxChars ? value.slice(0, maxChars) : value;
  }
  return undefined;
}

function updateCanonicalHash(digest: Hash, value: unknown, ancestors = new Set<object>()): void {
  if (value === null) {
    digest.update("null;");
    return;
  }
  if (typeof value === "string") {
    digest.update(`string:${value.length}:`).update(value).update(";");
    return;
  }
  if (typeof value !== "object") {
    const normalized = typeof value === "number" && !Number.isFinite(value) ? String(value) : value;
    digest.update(`${typeof value}:`).update(JSON.stringify(normalized) ?? String(normalized)).update(";");
    return;
  }
  if (ancestors.has(value)) {
    digest.update("circular;");
    return;
  }

  ancestors.add(value);
  if (Array.isArray(value)) {
    digest.update("array:[");
    for (const item of value) {
      updateCanonicalHash(digest, item, ancestors);
    }
    digest.update("];");
  } else {
    digest.update("object:{");
    const record = value as Record<string, unknown>;
    for (const key of Object.keys(record).sort()) {
      digest.update(`key:${key.length}:`).update(key).update(";");
      updateCanonicalHash(digest, record[key], ancestors);
    }
    digest.update("};");
  }
  ancestors.delete(value);
}

export function lifecycleEventIdentity(
  messages: unknown[],
  marker: { runId?: string; messageCount?: number; compactingCount?: number } = {},
): string {
  if (marker.runId) {
    return createHash("sha256").update("run\0").update(marker.runId).digest("hex");
  }
  const digest = createHash("sha256")
    .update("compaction\0")
    .update(String(marker.messageCount ?? messages.length))
    .update("\0")
    .update(marker.compactingCount === undefined ? "" : String(marker.compactingCount))
    .update("\0");
  updateCanonicalHash(digest, messages);
  return digest.digest("hex");
}

export function deterministicSourceId(params: {
  agentId: string;
  opaqueSessionId?: string;
  eventIdentity: string;
  content: string;
}): string {
  const digest = createHash("sha256")
    .update(params.agentId)
    .update("\0")
    .update(params.opaqueSessionId ?? "")
    .update("\0")
    .update(params.eventIdentity)
    .update("\0")
    .update(params.content)
    .digest("hex");
  return `openclaw-turn-${digest}`;
}

export function truncateUtf8(text: string, maxBytes: number): string {
  const encoded = Buffer.from(text, "utf8");
  if (encoded.byteLength <= maxBytes) {
    return text;
  }
  return encoded.subarray(0, maxBytes).toString("utf8").replace(/\uFFFD$/u, "");
}

export function escapePowerContextBoundary(text: string): string {
  return text.replace(/<\/?powercontext_memory>/giu, (tag) =>
    tag.replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
  );
}
