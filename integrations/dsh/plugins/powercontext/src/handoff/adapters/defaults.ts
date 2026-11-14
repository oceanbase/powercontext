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

import { accessSync, constants, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, isAbsolute, join } from 'node:path'
import { buildReceiverPrompt } from '../receiver-prompt.ts'
import type { TargetAdapter } from '../targets.ts'
import { createCodexDesktopAdapter } from './codex-desktop.ts'
import { createCodexDesktopRuntime } from './codex-desktop-runtime.ts'
import { createClaudeDesktopAdapter } from './claude-desktop.ts'
import { createDeferredClaudeCodeCliTarget } from './claude-code-cli.ts'
import { createClaudeCodeCliReceiver } from './claude-code-cli-runtime.ts'

const CODEX = { targetVersion: '26.1106.51428 (26605); codex-cli 0.167.0-alpha.4.1', evidence: 'HANDOFF_VALIDATION.md (5cd2)' }
const CLAUDE_CLI = { targetVersion: '2.1.323', evidence: 'HANDOFF_VALIDATION.md (d093)' }

export interface DefaultTargetAdaptersOptions {
  /** Profile-specific Host root and its persisted connection key. */
  readonly root: string
  readonly generationKey: Buffer
  readonly sourceServerUrl?: string
  readonly powercontextExecutable?: string
  readonly codexHome?: string
  readonly codexAppPath?: string
  readonly codexSource?: string
}

function installedExecutable(name: string, path: string): string | undefined {
  for (const directory of path.split(delimiter)) {
    if (!isAbsolute(directory)) continue
    const candidate = join(directory, name)
    try {
      accessSync(candidate, constants.X_OK)
      if (statSync(candidate).isFile()) return candidate
    } catch { /* absent or inaccessible in this directory */ }
  }
  return undefined
}

/**
 * The Host constructs this inside its profile lock. New native targets can be added
 * here once their own adapters exist; absence of qualification is not absence of detection.
 */
export function createDefaultTargetAdapters(options: DefaultTargetAdaptersOptions): TargetAdapter[] {
  if (!isAbsolute(options.root) || !Buffer.isBuffer(options.generationKey) || options.generationKey.length !== 32) {
    throw new Error('Default target adapters require an absolute Host root and persistent 32-byte key')
  }
  const home = homedir()
  // An app launched from Finder may not inherit ~/.local/bin (uv tools and native Claude).
  const path = [...(process.env.PATH ?? '').split(delimiter), join(home, '.local/bin'), '/opt/homebrew/bin',
    '/usr/local/bin', '/usr/bin', '/bin'].filter(isAbsolute).join(delimiter)
  const executable = options.powercontextExecutable ?? installedExecutable('powercontext', path)
  const appPath = options.codexAppPath ?? '/Applications/ChatGPT.app'
  const codexHome = options.codexHome ?? process.env.CODEX_HOME ?? join(home, '.codex')
  const setup = executable && options.sourceServerUrl
    ? { executable, serverUrl: options.sourceServerUrl, source: options.codexSource, path }
    : undefined
  const deliveryRoot = join(options.root, 'deliveries')
  const runtime = createCodexDesktopRuntime({
    cliPath: join(appPath, 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'),
    codexHome,
    deliveryRoot,
    generationKey: options.generationKey,
    setup,
  })
  // The macOS link starts the normal user profile, not a DSH-only CLAUDE_CONFIG_DIR or credential.
  const configDir = join(home, '.claude')
  const environment = { HOME: home, PATH: path }
  const receiver = createClaudeCodeCliReceiver({
    configDir, launchEnvironment: environment, pythonExecutable: '/usr/bin/python3',
    generationKey: options.generationKey, environmentEvidence: { terminal: 'macOS', evidence: CLAUDE_CLI.evidence },
    qualifiedPackageRoots: [deliveryRoot], observeDefaultTerminal: true,
  })
  return [createCodexDesktopAdapter({
    appPath, runtime, buildReceiverPrompt, qualification: CODEX,
  }), createClaudeDesktopAdapter(), createDeferredClaudeCodeCliTarget({
    receiver, buildReceiverPrompt, path, qualification: CLAUDE_CLI,
    integrationOptions: { configDir, environment, powercontextExecutable: executable, serverUrl: options.sourceServerUrl },
  })]
}
