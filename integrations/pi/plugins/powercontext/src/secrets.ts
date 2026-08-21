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

const SECRET_PATTERNS = [
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)/giu,
  /(?<![\w-])["']?\b(?:api[_ -]?key|access[_ -]?key|client[_ -]?secret|secret(?:[_ -]?key)?|password|passwd|passphrase|token|authorization|cookie)\b["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;}\]]+)/giu,
  /(?<![\w-])bearer\s+[A-Za-z0-9._~+/=-]{8,}(?![\w-])/giu,
  /(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}|github_pat_[A-Za-z0-9_]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})(?![\w-])/giu,
] as const

/** Return text with recognized credential-shaped values replaced. */
export function scrubSecrets(text: string): string {
  return SECRET_PATTERNS.reduce((scrubbed, pattern) => scrubbed.replace(pattern, '[REDACTED]'), text)
}

/** Detect secrets without rejecting ordinary words that merely contain a marker. */
export function containsSecret(text: string): boolean {
  return scrubSecrets(text) !== text
}
