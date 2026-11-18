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

// Generated from integrations/lifecycle/prompt-privacy.ts; do not edit.
// Edit the source and run `make integration-lifecycle` instead.

// The automatic prompt-privacy rule. tests/fixtures/automatic_prompt_privacy.json lists the cases every host must
// handle the same way.
const SECRET_PATTERNS = [
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)/giu,
  // Consume complete headers before the assignment rule can consume just a scheme or first cookie. A hyphenated prefix
  // such as Proxy-, Set-, or X-Forwarded-, or the CGI form HTTP_, names another header of the same kind. A boolean or
  // null literal or an unexpanded ${NAME} or ${{ expression }} placeholder is a setting, not a credential, when no
  // more of the header follows it; ${NAME:-value} is not excluded. An assigned value starts on the separator's line
  // or on an indented continuation line. Horizontal space is spelled out so that Python's ASCII \s agrees: NBSP and
  // the full-width space separate a value just as a space does.
  /(?<![\w-])["']?(?:HTTP(?:_[A-Za-z0-9]+)*_|(?:[A-Za-z0-9]+-)*)(?:authorization|cookie)\b["'`*]*[ \t]*[:=]\**(?![ \t]*(?:true|false|yes|no|on|off|null|none|(?:\$\{\{[^{}\r\n]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})|"(?:\$\{\{[^{}\r\n]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})"|'(?:\$\{\{[^{}\r\n]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})')(?:[`'")\]}]|[,.]?(?:[\s \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]|$)))[ \t]*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*/giu,
  // A key name may carry any prefix, as in OPENAI_API_KEY, X-Api-Key, --password, or dbPassword, but must end with the
  // marker, so max_tokens and password_min_length stay ordinary. Markdown emphasis or code around the name, as in
  // **Password:** or `DB_PASSWORD`:, is part of it. Tokenizer special tokens such as pad_token are configuration. A
  // scheme word belongs to the value that follows it.
  /(?<![\w-])["']?(?!(?:bos|eos|pad|unk|sep|cls|mask)_token\b)[\w-]*?(?:api[_ -]?key|access[_ -]?(?:key(?:[_-]id)?|token)|client[_ -]?secret|secret(?:[_ -]?key)?|private[_ -]?key|password|passwd|passphrase|token|authorization|cookie)\b["'`*]*[\s \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*[:=]\**(?![ \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*(?:\r?\n[ \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+)?(?:true|false|yes|no|on|off|null|none|(?:\$\{\{[^{}\r\n]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})|"(?:\$\{\{[^{}\r\n]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})"|'(?:\$\{\{[^{}\r\n]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})')(?:[\s,;)\]}`'" \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]|\.(?!\S)|$))[ \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*(?:\r?\n[ \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+)?(?:(?:bearer|basic)[ \t]+)?(?:"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;}\] \t\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+)/giu,
  /(?<![\w-])bearer\s+[A-Za-z0-9._~+/=-]{8,}(?![\w-])/giu,
  /(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}|[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})(?![\w-])/giu,
] as const

/** Return text with recognized credential-shaped values replaced. */
export function scrubSecrets(text: string, authorization?: string): string {
  const credential = authorization?.replace(/^Bearer\s+/iu, '')
  if (credential) text = text.replaceAll(credential, '[REDACTED]')
  return SECRET_PATTERNS.reduce((scrubbed, pattern) => scrubbed.replace(pattern, '[REDACTED]'), text)
}

/** Detect secrets without rejecting ordinary words that merely contain a marker. */
export function containsSecret(text: string, authorization?: string): boolean {
  return scrubSecrets(text, authorization) !== text
}
