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

export function deterministicSourceId(params: {
  agentId: string;
  opaqueSessionId?: string;
  content: string;
}): string {
  const digest = createHash("sha256")
    .update(params.agentId)
    .update("\0")
    .update(params.opaqueSessionId ?? "")
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
