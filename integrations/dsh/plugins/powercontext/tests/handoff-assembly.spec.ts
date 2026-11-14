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
import { mkdtempSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, it, vi } from 'vitest'
import type { PowerContextClient } from '../src/client.ts'
import { createHandoffServices } from '../src/handoff/assembly.ts'
import type { PreflightAuthority } from '../src/handoff/preflight.ts'
import { TargetCatalog } from '../src/handoff/targets.ts'

it('can retry service assembly after a receipt directory problem is fixed', async () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-assembly-recovery-'))
  const client = { request: vi.fn() } as unknown as Pick<PowerContextClient, 'request'>
  const options = { root, client, authority: {} as PreflightAuthority, catalog: new TargetCatalog([]),
    signal: new AbortController().signal, resolveReceiptConnection: async () => ({ client, accessMode: 'disabled' as const }) }
  try {
    writeFileSync(join(root, 'receipts'), 'not a directory', { mode: 0o600 })
    expect(() => createHandoffServices(options)).toThrow()
    unlinkSync(join(root, 'receipts'))
    const recovered = createHandoffServices(options)
    await recovered.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
