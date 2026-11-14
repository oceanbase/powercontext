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

import type { HandoffLocale } from '../locale.ts'
import { homedir } from 'node:os'
import { isAbsolute, join } from 'node:path'
import type { LaunchBundle } from '../contracts.ts'
import type { TargetAdapter } from '../targets.ts'
import { createCodexDesktopAdapter } from './codex-desktop.ts'
import { createCodexDesktopRuntime } from './codex-desktop-runtime.ts'
import { createClaudeDesktopAdapter } from './claude-desktop.ts'
import { createClaudeCodeCliAdapter } from './claude-code-cli.ts'

export interface DefaultTargetAdaptersOptions {
  /** Profile-specific Host root and its persisted connection key. */
  readonly root: string
  readonly generationKey: Buffer
  readonly sourceServerUrl?: string
  readonly powercontextExecutable?: string
  readonly codexHome?: string
  readonly codexAppPath?: string
  readonly codexSource?: string
  readonly buildReceiverPrompt?: (bundle: LaunchBundle, locale?: HandoffLocale) => string
  /** Supply only real, version-specific host acceptance evidence. */
  readonly codexQualification?: { readonly evidence: string; readonly targetVersion: string }
}

/**
 * The Host constructs this inside its profile lock. New native targets can be added
 * here once their own adapters exist; absence of qualification is not absence of detection.
 */
export function createDefaultTargetAdapters(options: DefaultTargetAdaptersOptions): TargetAdapter[] {
  if (!isAbsolute(options.root) || !Buffer.isBuffer(options.generationKey) || options.generationKey.length !== 32) {
    throw new Error('Default target adapters require an absolute Host root and persistent 32-byte key')
  }
  const appPath = options.codexAppPath ?? '/Applications/ChatGPT.app'
  const codexHome = options.codexHome ?? process.env.CODEX_HOME ?? join(homedir(), '.codex')
  const setup = options.powercontextExecutable && options.sourceServerUrl
    ? { executable: options.powercontextExecutable, serverUrl: options.sourceServerUrl, source: options.codexSource }
    : undefined
  const runtime = createCodexDesktopRuntime({
    cliPath: join(appPath, 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'),
    codexHome,
    deliveryRoot: join(options.root, 'deliveries'),
    generationKey: options.generationKey,
    setup,
  })
  return [createCodexDesktopAdapter({
    appPath, runtime,
    ...(options.buildReceiverPrompt ? { buildReceiverPrompt: options.buildReceiverPrompt } : {}),
    ...(options.codexQualification ? { qualification: options.codexQualification } : {}),
  }), createClaudeDesktopAdapter(), createClaudeCodeCliAdapter()]
}
