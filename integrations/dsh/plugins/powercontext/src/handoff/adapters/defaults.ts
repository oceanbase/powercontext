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
import { createHostCodexDesktopAdapter } from './codex-desktop-runtime.ts'
import { createClaudeDesktopAdapter } from './claude-desktop.ts'
import { createDeferredClaudeCodeCliTarget } from './claude-code-cli.ts'
import { createClaudeCodeCliReceiver } from './claude-code-cli-runtime.ts'

const CODEX = { targetVersion: '26.1106.51428 (26605); codex-cli 0.167.0-alpha.4.1', evidence: 'HANDOFF_VALIDATION.md (5cd2)' }
const CLAUDE_CLI = { targetVersion: '2.1.323', evidence: 'HANDOFF_VALIDATION.md (d093)' }

export interface DefaultTargetAdaptersOptions {
  /** Delivery storage is available only after the Host acquires its writer lease. */
  readonly deliveryStorage: () => { deliveryRoot: string; generationKey: Buffer }
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
 * Target discovery needs neither the profile lock nor the connection key.
 * Only receiver environment and package checks use delivery storage.
 */
export function createDefaultTargetAdapters(options: DefaultTargetAdaptersOptions): TargetAdapter[] {
  const home = homedir()
  // An app launched from Finder may not inherit ~/.local/bin (uv tools and native Claude).
  const path = [...(process.env.PATH ?? '').split(delimiter), join(home, '.local/bin'), '/opt/homebrew/bin',
    '/usr/local/bin', '/usr/bin', '/bin'].filter(isAbsolute).join(delimiter)
  const executable = options.powercontextExecutable ?? installedExecutable('powercontext', path)
  const codexHome = options.codexHome ?? process.env.CODEX_HOME ?? join(home, '.codex')
  const setup = executable && options.sourceServerUrl
    ? { executable, serverUrl: options.sourceServerUrl, source: options.codexSource, path }
    : undefined
  // The macOS link starts the normal user profile, not a DSH-only CLAUDE_CONFIG_DIR or credential.
  const configDir = join(home, '.claude')
  const environment = { HOME: home, PATH: path }
  const receiver = createClaudeCodeCliReceiver({
    configDir, launchEnvironment: environment, pythonExecutable: '/usr/bin/python3',
    environmentEvidence: { terminal: 'Terminal', evidence: CLAUDE_CLI.evidence },
    packageStorage: () => {
      const { deliveryRoot, generationKey } = options.deliveryStorage()
      return { generationKey, qualifiedPackageRoots: [deliveryRoot] }
    },
  })
  return [createHostCodexDesktopAdapter({
    appPath: options.codexAppPath, codexHome, deliveryStorage: options.deliveryStorage,
    setup, buildReceiverPrompt, qualification: CODEX,
  }), createClaudeDesktopAdapter(), createDeferredClaudeCodeCliTarget({
    receiver, buildReceiverPrompt, path, qualification: CLAUDE_CLI,
    integrationOptions: { configDir, environment, powercontextExecutable: executable, serverUrl: options.sourceServerUrl },
  })]
}
