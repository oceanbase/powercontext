/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *\n * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { homedir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import { buildReceiverPrompt } from '../src/handoff/receiver-prompt.ts'
import { nativeLaunchUrl } from '../src/handoff/native-launch.ts'
import { NATIVE_TARGET_IDS } from '../src/handoff/targets.ts'

const source = { sessionId: 's', messageId: 'm', throughSeq: 1, cwd: '/a', workspaceObservedAt: '2026-11-13T00:00:00Z' }
const bundle: LaunchBundle = { deliveryId: 'delivery', source, packagePath: '/tmp/交接.json', packageSha256: 'a'.repeat(64),
  binding: { source, preflightId: 'p', revision: 1, targetId: 't', targetVersion: '1', adapterVersion: '1', effectiveCwd: '/a', serverIdentity: 'server', connectionRef: 'private-ref', connectionGeneration: 1, scopeId: 'scope' },
  handoffSelection: { kind: 'prepared', digest: 'sha256:' + 'b'.repeat(64) },
  receiptAttempt: { receiptAttemptId: 'attempt', sourceType: 'content', sourceId: 'dsh-ack:attempt' } }

describe('receiver prompt', () => {
  it('shows only the continuation request and an editable package reference', () => {
    const prompt = buildReceiverPrompt(bundle)
    expect(prompt.split('\n')).toEqual([
      'Please use PowerContext to read this handoff package and continue the remaining work. Reply in English:',
      JSON.stringify(bundle.packagePath),
    ])
    for (const detail of [bundle.packageSha256, bundle.binding.scopeId, bundle.binding.connectionRef, bundle.receiptAttempt.sourceId, 'continue_handoff']) {
      expect(prompt).not.toContain(detail)
    }
  })
  it('does not truncate oversized prompts', () => {
    expect(() => buildReceiverPrompt({ ...bundle, packagePath: '/tmp/' + '字'.repeat(1000) })).toThrow()
  })
  it('carries the short Chinese draft and exact path through every native URL', () => {
    const suffix = '.powercontext-dsh-handoff/' + 'f'.repeat(64) + '/deliveries/12345678-0000-4000-8000-000000000000.package.json'
    const value = { ...bundle, packagePath: join(homedir(), suffix) }
    const draft = buildReceiverPrompt(value, 'zh-CN')
    expect(draft.split('\n')).toEqual([
      '请用 PowerContext 读取这份交接包，继续剩下的工作，并用简体中文回复：',
      JSON.stringify('~/' + suffix),
    ])
    for (const target of NATIVE_TARGET_IDS) {
      const url = new URL(nativeLaunchUrl(target, '/Users/qa/sample-workspace', { draft }))
      expect(url.searchParams.get(target === 'codex-desktop' ? 'prompt' : 'q')).toBe(draft)
    }
  })
})
