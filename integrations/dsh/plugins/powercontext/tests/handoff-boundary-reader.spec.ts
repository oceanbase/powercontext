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

import { describe, expect, it, vi } from 'vitest'
import { createBoundaryReader } from '../src/handoff/boundary-reader.ts'
import { collectMaterials } from '../src/handoff/materials.ts'

const source = { sessionId: 'session', messageId: 'answer', throughSeq: 5, cwd: '/work',
  workspaceObservedAt: new Date(6).toISOString() }
const rows = [
  { seq: 0, time: 1, type: 'turn/start', data: { turn: 1 } },
  { seq: 1, time: 2, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Fix the defect' }] } },
  { seq: 2, time: 3, type: 'step/start', data: { turn: 1 } },
  { seq: 3, time: 4, type: 'assistant/message', data: { turn: 1, message: { id: 'answer', content: [{ type: 'text', text: 'Work in progress' }] } } },
  { seq: 4, time: 5, type: 'step/end', data: { turn: 1 } },
  { seq: 5, time: 6, type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } },
]
const context = { ownerId: 'owner', signal: new AbortController().signal }
function fixture(events = rows) {
  const snapshotEvents = vi.fn((from: number, to: number) => events.slice(from, to))
  const session = { header: { id: 'session' }, seq: events.length, snapshotEvents }
  const host = { get: (name: string) => name === 'sessions' ? { get: (id: string) => id === 'session' ? session : undefined }
    : name === 'workingDirectory' ? { get: () => '/work' } : undefined }
  return { read: createBoundaryReader(host as never), snapshotEvents }
}

describe('DSH boundary reader', () => {
  it('reads only the fixed session and boundary, retaining honest omissions', async () => {
    const { read, snapshotEvents } = fixture([...rows, { seq: 6, time: 7, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'later task' }] } }])
    const materials = await collectMaterials(context, source, read, 32768)
    expect(materials?.objective).toBe('Fix the defect')
    const content = JSON.parse(materials!.serialized)
    expect(content.selectedReply).toMatchObject({ messageId: 'answer', seq: 3, text: 'Work in progress' })
    expect(content.workspace).toBeNull()
    expect(content.omissions.join(' ')).toContain('workspace')
    expect(content.omissions.join(' ')).toContain('check')
    expect(snapshotEvents.mock.calls.every(([from, to]) => to === from + 1 && to <= 6)).toBe(true)
  })

  it('rejects a missing, unfinished or mismatched reply and incorrect cutoff', async () => {
    const { read } = fixture()
    const limits = { maxEvents: 511, maxBytes: 32768, maxWorkspaceBytes: 8192 }
    await expect(read(context, { ...source, messageId: 'other' }, limits)).rejects.toMatchObject({ code: 'binding_mismatch' })
    await expect(read(context, { ...source, workspaceObservedAt: new Date(7).toISOString() }, limits)).rejects.toMatchObject({ code: 'binding_mismatch' })
    const unfinished = fixture(rows.map(row => row.seq === 5 ? { ...row, data: { turn: 1, reason: { kind: 'cancelled' } } } : row))
    await expect(unfinished.read(context, source, limits)).rejects.toMatchObject({ code: 'binding_mismatch' })
  })

  it('does not turn plugin-injected text into a human objective', async () => {
    const plugin = fixture(rows.map(row => row.seq === 1 ? { ...row, data: { source: { kind: 'plugin' }, content: [{ type: 'text', text: 'injected' }] } } : row))
    const value = await plugin.read(context, source, { maxEvents: 511, maxBytes: 32768, maxWorkspaceBytes: 8192 })
    expect(value.objective).toBeNull()
    expect(await collectMaterials(context, source, plugin.read, 32768)).toBeUndefined()
  })
})

it('keeps original human request, later instructions, assistant reports, and bounded check output without inventing a pass', async () => {
  const rows: any[] = [
    { seq: 0, time: 1, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Fix the original regression' }] } },
    { seq: 1, time: 2, type: 'assistant/message', data: { turn: 1, message: { id: 'earlier', content: [{ type: 'text', text: 'The first change is in place.' }] } } },
    { seq: 2, time: 3, type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } },
    { seq: 3, time: 4, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Continue, but do not change the profile.' }] } },
    { seq: 4, time: 5, type: 'tool/call', data: { turn: 2, step: 1, callId: 'check', name: 'bash', arguments: JSON.stringify({ command: 'pnpm test' }) } },
    { seq: 5, time: 6, type: 'tool/result', data: { turn: 2, step: 1, message: { toolCallId: 'check', content: [{ type: 'text', text: '2 tests failed\n[exit code: 1]' }] } } },
    { seq: 6, time: 7, type: 'assistant/message', data: { turn: 2, message: { id: 'answer', content: [{ type: 'text', text: 'The failure remains.' }] } } },
    { seq: 7, time: 8, type: 'turn/end', data: { turn: 2, reason: { kind: 'completed' } } },
    { seq: 8, time: 9, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'A different later task' }] } },
  ]
  const session = { header: { id: 'session' }, seq: rows.length, snapshotEvents: (from: number, to: number) => rows.slice(from, to) }
  const read = createBoundaryReader({ get: (name: string) => name === 'sessions' ? { get: () => session } : { get: () => '/work' } } as never)
  const boundary = { ...source, throughSeq: 7, workspaceObservedAt: new Date(8).toISOString() }
  const result = await collectMaterials(context, boundary, read, 32768)
  expect(result?.objective).toBe('Fix the original regression')
  const material = JSON.parse(result!.serialized)
  expect(material.selectedReply.text).toBe('The failure remains.')
  expect(material.events).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'observation', text: expect.stringContaining('do not change the profile') }),
    expect.objectContaining({ kind: 'observation', text: expect.stringContaining('The first change is in place') }),
    expect.objectContaining({ kind: 'check', command: 'pnpm test', status: 'unknown', details: expect.stringContaining('2 tests failed') }),
  ]))
  expect(JSON.stringify(material)).not.toContain('A different later task')
  expect(material.omissions.join(' ')).toContain('exit status')
})

it('requires manual input when the original objective is outside the bounded event window', async () => {
  const rows = Array.from({ length: 520 }, (_, seq) => ({ seq, time: seq + 1, type: 'other', data: {} })) as any[]
  rows[510] = { seq: 510, time: 511, type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: 'Continue' }] } }
  rows[518] = { seq: 518, time: 519, type: 'assistant/message', data: { turn: 1, message: { id: 'answer', content: [{ type: 'text', text: 'Done for now' }] } } }
  rows[519] = { seq: 519, time: 520, type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } }
  const session = { header: { id: 'session' }, seq: rows.length, snapshotEvents: (from: number, to: number) => rows.slice(from, to) }
  const read = createBoundaryReader({ get: (name: string) => name === 'sessions' ? { get: () => session } : { get: () => '/work' } } as never)
  const boundary = { ...source, throughSeq: 519, workspaceObservedAt: new Date(520).toISOString() }
  expect(await collectMaterials(context, boundary, read, 32768)).toBeUndefined()
})

it('retains a selected reply longer than 8 KiB when it fits the generation budget', async () => {
  const longReply = 'x'.repeat(9000)
  const changed = rows.map(row => row.seq === 3 ? { ...row, data: { turn: 1, message: { id: 'answer', content: [{ type: 'text', text: longReply }] } } } : row)
  const result = await collectMaterials(context, source, fixture(changed).read, 32768)
  expect(JSON.parse(result!.serialized).selectedReply.text).toBe(longReply)
})
