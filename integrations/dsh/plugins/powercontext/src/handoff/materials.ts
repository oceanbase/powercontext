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

import type { CallContext, SourceBoundary } from './contracts.ts'
import { DeliveryError } from './contracts.ts'
import { hasSecretContent } from '../secrets.ts'
import { invalid, trimmed, wire } from './wire.ts'
import type { Wire } from './wire.ts'

export const MAX_EVENTS = 512
export const MAX_MATERIAL_BYTES = 256 * 1024
export const MAX_WORKSPACE_BYTES = 32 * 1024
export const MAX_WORK_INPUT_BYTES = 32 * 1024

type Citation = Wire<'HandoffCitation'>

export type WorkEvent = {
  sessionId: string
  seq: number
  observedAt?: string
  evidence?: Citation[]
} & (
  | { kind: 'objective' | 'constraint' | 'completed' | 'next_action' | 'unresolved_error'; text: string }
  | { kind: 'check'; command: string; status: 'passed' | 'failed' | 'interrupted' | 'not_run' | 'unknown';
    exitCode?: number; revision?: string; workspaceObservedAt?: string; details?: string }
)

export interface BoundaryEvidence {
  source: SourceBoundary
  objective: string | null
  selectedReply: { messageId: string; seq: number; text: string }
  /** Recent or relevant evidence first. The reader must bound the underlying Host reads too. */
  events: Iterable<WorkEvent> | AsyncIterable<WorkEvent>
  workspace: { observedAt: string; revision: string | null; uncommittedChanges: string | null } | null
  omissions?: readonly string[]
}

/** Reads trusted Host observations at this exact boundary, never a live/latest reply or workspace substitute. */
export type BoundaryReader = (
  context: CallContext, source: SourceBoundary, limits: { maxEvents: number; maxBytes: number; maxWorkspaceBytes: number },
) => Promise<BoundaryEvidence>

export function boundary(value: SourceBoundary): SourceBoundary {
  if (!value || !trimmed(value.sessionId, 256) || !trimmed(value.messageId, 256)
    || !Number.isSafeInteger(value.throughSeq) || value.throughSeq < 0 || !trimmed(value.cwd, 4096)
    || value.cwd.includes('\0') || !trimmed(value.workspaceObservedAt, 64)
    || !Number.isFinite(Date.parse(value.workspaceObservedAt))) invalid()
  const { sessionId, messageId, throughSeq, cwd, workspaceObservedAt } = value
  return { sessionId, messageId, throughSeq, cwd, workspaceObservedAt }
}

export function sameBoundary(a: SourceBoundary, b: SourceBoundary): boolean {
  return a.sessionId === b.sessionId && a.messageId === b.messageId && a.throughSeq === b.throughSeq
    && a.cwd === b.cwd && a.workspaceObservedAt === b.workspaceObservedAt
}

function secret(value: unknown): boolean {
  return hasSecretContent('capture_content_source', value)
}

export function inspectedWork(value: unknown): Wire<'CurrentWorkHandoff'> {
  const work = wire('CurrentWorkHandoff', value)
  if (!trimmed(work.objective, 8192) || !work.omissions.every(item => trimmed(item, 8192))
    || new Set(work.omissions).size !== work.omissions.length
    || [ ...work.state, ...(work.next_action ? [work.next_action] : []) ].some(claim =>
      !trimmed(claim.text, 8192) || (claim.basis === 'verified') !== (claim.evidence.length > 0))
    || secret(work) || Buffer.byteLength(JSON.stringify(work)) > MAX_WORK_INPUT_BYTES) invalid()
  return work
}

async function* boundedEvents(events: BoundaryEvidence['events']) {
  let remaining = MAX_EVENTS - 1 // The selected reply counts toward the bound.
  for await (const event of events) {
    yield event
    if (--remaining === 0) return
  }
}

export async function collectMaterials(context: CallContext, source: SourceBoundary, read: BoundaryReader, limit: number) {
  // The HTTP Source also has a 200,000-character limit. Reserve space for metadata and omissions.
  const maxBytes = Math.min(limit, MAX_MATERIAL_BYTES, 192 * 1024)
  const evidence = await read(context, source, {
    maxEvents: MAX_EVENTS - 1, maxBytes, maxWorkspaceBytes: Math.min(maxBytes, MAX_WORKSPACE_BYTES),
  })
  context.signal.throwIfAborted()
  const { selectedReply, objective } = evidence
  if (!sameBoundary(source, evidence.source) || selectedReply.messageId !== source.messageId
    || !Number.isSafeInteger(selectedReply.seq) || selectedReply.seq < 0 || selectedReply.seq > source.throughSeq) {
    throw new DeliveryError('binding_mismatch')
  }
  const omissions = new Set<string>()
  const note = (text: string) => { omissions.add(text) }
  const available = (text: string | null, max: number) => typeof text === 'string'
    && text.length <= max && !/[\uD800-\uDFFF]/u.test(text) && !secret(text) && Buffer.byteLength(text) <= max
  if (!trimmed(objective, 8192) || !available(objective, Math.min(8192, maxBytes / 4))) {
    return undefined // Editable work fields are required. Do not capture or guess an objective.
  }
  const reply = { messageId: selectedReply.messageId, seq: selectedReply.seq,
    text: available(selectedReply.text, maxBytes / 3) ? selectedReply.text : null }
  if (reply.text === null) note('The selected reply was unavailable within the content and input limits.')
  let workspace = null
  if (evidence.workspace?.observedAt === source.workspaceObservedAt) {
    const { observedAt, revision, uncommittedChanges } = evidence.workspace
    workspace = { observedAt, revision: available(revision, 256) ? revision : null,
      uncommittedChanges: available(uncommittedChanges, Math.min(MAX_WORKSPACE_BYTES, maxBytes / 4)) ? uncommittedChanges : null }
    if (Buffer.byteLength(JSON.stringify(workspace)) > MAX_WORKSPACE_BYTES) workspace.uncommittedChanges = null
    if (workspace.revision === null || workspace.uncommittedChanges === null) note('Workspace revision or uncommitted changes were not available.')
  } else {
    note('The workspace observation at the selected time was not available.')
  }
  const events: WorkEvent[] = []
  const seen = new Set<number>([selectedReply.seq])
  const content = { source, objective, selectedReply: reply, workspace, events, omissions: [] as string[] }
  let remaining = maxBytes - Buffer.byteLength(JSON.stringify(content)) - 1024
  if (remaining < 0) return undefined
  let count = 1
  let checksObserved = false
  for await (const event of boundedEvents(evidence.events)) {
    context.signal.throwIfAborted()
    if (++count === MAX_EVENTS) note('The session evidence window reached the event limit.')
    if (event.sessionId !== source.sessionId || !Number.isSafeInteger(event.seq)
      || event.seq < 0 || event.seq > source.throughSeq) throw new DeliveryError('binding_mismatch')
    if (seen.has(event.seq)) continue
    seen.add(event.seq)
    const { sessionId, seq, observedAt, evidence: refs, kind } = event
    const base = { sessionId, seq, observedAt: trimmed(observedAt, 64) ? observedAt : undefined,
      evidence: refs?.slice(0, 32).map(ref => wire('HandoffCitation', ref)) }
    let item: WorkEvent
    if (kind === 'check') {
      checksObserved = true
      const { command, status, details, exitCode, revision, workspaceObservedAt } = event
      if (!['passed', 'failed', 'interrupted', 'not_run', 'unknown'].includes(status)) invalid()
      if (!available(command, 8192)) {
        note(status === 'failed'
          ? 'At least one observed check failed but its command could not be included.'
          : 'A check was observed but its command could not be included.')
        continue
      }
      item = { ...base, kind, command, status,
        details: details && available(details, 8192) ? details : undefined,
        revision: revision && available(revision, 256) ? revision : undefined,
        workspaceObservedAt: trimmed(workspaceObservedAt, 64) ? workspaceObservedAt : undefined,
        exitCode: Number.isSafeInteger(exitCode) ? exitCode : undefined }
      if (details && item.details === undefined) note('Some check details could not be included.')
    } else {
      if (!available(event.text, 8192)) {
        note('Some task evidence was omitted because of content or input limits.')
        continue
      }
      item = { ...base, kind, text: event.text }
    }
    const bytes = Buffer.byteLength(JSON.stringify(item)) + 1
    if (secret(item) || bytes > remaining) {
      note(kind === 'check' && item.kind === 'check' && item.status === 'failed'
        ? 'At least one observed failure could not fit the input limits or contained sensitive material.'
        : 'Some task evidence was omitted because of content or input limits.')
      continue
    }
    remaining -= bytes
    events.push(item)
  }
  if (!checksObserved) note('No check evidence was available; whether checks ran is unknown.')
  if (workspace && events.some(event => event.kind === 'check'
    && (event.revision && workspace.revision && event.revision !== workspace.revision
      || event.workspaceObservedAt && event.workspaceObservedAt !== workspace.observedAt))) {
    note('Some checks describe a different revision or workspace state from the selected observation.')
  }
  if (events.some(event => event.kind === 'check' && !event.revision && !event.workspaceObservedAt)) {
    note('Some checks could not be tied to an exact workspace state.')
  }
  const hostOmissions = evidence.omissions ?? []
  const maxHostOmissions = 16
  let hostOmissionsTruncated = hostOmissions.length > maxHostOmissions
  for (const text of hostOmissions.slice(0, maxHostOmissions)) {
    const size = typeof text === 'string' ? Buffer.byteLength(JSON.stringify(text)) + 1 : 0
    if (available(text, 256) && size <= remaining) {
      remaining -= size
      note(text)
    } else {
      hostOmissionsTruncated = true
    }
  }
  if (hostOmissionsTruncated) note('Additional omissions reported by the Host could not be included.')
  content.omissions = [...omissions]
  const serialized = JSON.stringify(content)
  if (Buffer.byteLength(serialized) > maxBytes) invalid()
  return { objective, serialized }
}
