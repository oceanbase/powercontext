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

import type { Context } from '@deepseek-ai/cordis'
import { DeliveryError } from './contracts.ts'
import type { BoundaryReader, WorkEvent } from './materials.ts'

interface Message {
  id?: string
  toolCallId?: string
  isError?: boolean
  source?: { kind?: string; callId?: string }
  content?: readonly { type?: string; text?: string; isError?: boolean }[]
}
interface Event {
  seq: number
  time: number
  type: string
  data?: Message & { turn?: number; step?: number; interrupted?: boolean; message?: Message;
    reason?: { kind?: string }; callId?: string; name?: string; arguments?: string; error?: unknown }
}
interface Session {
  header: { id: string; origin?: string }
  seq: number
  snapshotEvents(from: number, toExclusive: number): readonly Event[]
}

/** Reads only the selected local session's fixed event window. The caller must run Host preflight first. */
export function createBoundaryReader(host: Pick<Context, 'get'>): BoundaryReader {
  return async (context, source, limits) => {
    context.signal.throwIfAborted()
    const sessions = host.get('sessions') as { get(id: string): Session | undefined } | undefined
    const session = sessions?.get(source.sessionId)
    if (!session || session.header.id !== source.sessionId || session.header.origin === 'subagent') {
      throw new DeliveryError('not_found')
    }
    if (!Number.isSafeInteger(source.throughSeq) || source.throughSeq < 0 || source.throughSeq >= session.seq
      || !Number.isSafeInteger(limits.maxEvents) || limits.maxEvents < 1
      || !Number.isSafeInteger(limits.maxBytes) || limits.maxBytes < 1024) throw new DeliveryError('binding_mismatch')
    const workingDirectory = host.get('workingDirectory') as { get?: (session: Session) => string } | undefined
    if (!workingDirectory?.get) throw new DeliveryError('integration_unavailable')
    if (workingDirectory.get(session) !== source.cwd) throw new DeliveryError('binding_mismatch')

    // One event per read: DSH's snapshot API has no byte-budget argument.
    const from = Math.max(0, source.throughSeq - Math.min(limits.maxEvents, 511))
    const label = (en: string, zh: string) => limits.locale === 'zh-CN' ? zh : en
    const omissions = new Set<string>([label('The workspace observation at the selected time was not recorded by DSH.', 'DSH 未记录所选时点的工作区状态。')])
    if (from > 0) omissions.add(label('Earlier session events and the original objective may be outside the bounded evidence window.', '较早的会话事件和原始目标可能超出取证范围。'))
    const ended = new Set<number>()
    const events: WorkEvent[] = []
    const pendingResults = new Map<string, Event>()
    let selected: { messageId: string; seq: number; text: string } | undefined
    let objective: string | null = null
    let selectedUnavailable = false
    let remaining = limits.maxBytes
    const text = (message: Message | undefined, max: number): string | undefined => {
      if (!Array.isArray(message?.content)) return undefined
      const parts: string[] = []
      let size = 0
      for (const part of message.content) {
        if (part.type !== 'text' || typeof part.text !== 'string') continue
        size += Buffer.byteLength(part.text) + (parts.length ? 1 : 0)
        if (size > max || size > remaining) {
          omissions.add(label('Some message or tool content exceeded the bounded evidence limits.', '部分消息或工具内容超出取证上限。'))
          return undefined
        }
        parts.push(part.text)
      }
      const value = parts.join('\n')
      if (!value.trim()) return undefined
      remaining -= size
      return value
    }
    const observe = (event: Event, textValue: string) => {
      events.push({ sessionId: source.sessionId, seq: event.seq, kind: 'observation', text: textValue })
    }
    for (let seq = source.throughSeq; seq >= from; seq--) {
      context.signal.throwIfAborted()
      const row = session.snapshotEvents(seq, seq + 1)
      if (row.length !== 1 || row[0].seq !== seq) throw new DeliveryError('binding_mismatch')
      const event = row[0]
      if (seq === source.throughSeq && (!Number.isSafeInteger(event.time)
        || event.time !== Date.parse(source.workspaceObservedAt))) throw new DeliveryError('binding_mismatch')
      if (event.type === 'turn/end' && event.data?.reason?.kind === 'completed'
        && typeof event.data.turn === 'number') ended.add(event.data.turn)
      if (event.type === 'assistant/message' && event.data?.message?.id === source.messageId
        && typeof event.data.turn === 'number' && !event.data.interrupted && ended.has(event.data.turn)) {
        const reply = text(event.data.message, Math.floor(limits.maxBytes / 3))
        if (reply === undefined) selectedUnavailable = true
        selected = { messageId: source.messageId, seq, text: reply ?? '' }
      } else if (event.type === 'assistant/message' && event.data?.message?.id !== source.messageId) {
        const reply = text(event.data?.message, 8000)
        if (reply) observe(event, label(`Assistant reply (unverified): ${reply}`, `助手回复（未经核实）：${reply}`))
      }
      if (event.type === 'user/message' && event.data?.source?.kind === 'user') {
        const human = text(event.data, 8000)
        if (human) {
          // Events are scanned newest first, so the oldest retained human input wins.
          observe(event, label(`Human request or instruction: ${human}`, `用户请求或指令：${human}`))
          objective = human
        }
      }
      if (event.type === 'tool/result') {
        const id = event.data?.message?.toolCallId ?? event.data?.message?.source?.callId
        if (typeof id === 'string' && !pendingResults.has(id)) pendingResults.set(id, event)
      }
      if (event.type === 'tool/call' && typeof event.data?.callId === 'string') {
        const result = pendingResults.get(event.data.callId)
        if (!result || result.data?.turn !== event.data.turn || result.data?.step !== event.data.step) continue
        pendingResults.delete(event.data.callId)
        if (event.data.name !== 'bash' || typeof event.data.arguments !== 'string'
          || Buffer.byteLength(event.data.arguments) > 8192) {
          omissions.add(label('Some tool results could not be classified as checks.', '无法将部分工具结果归类为检查。'))
          continue
        }
        let command: unknown
        try { command = JSON.parse(event.data.arguments).command } catch { /* malformed Host input */ }
        if (typeof command !== 'string' || !command.trim() || Buffer.byteLength(command) > 8192) {
          omissions.add(label('A shell command could not be recovered within the evidence limits.', '在取证范围内无法找回一条 Shell 命令。'))
          continue
        }
        const commandBytes = Buffer.byteLength(command)
        if (commandBytes > remaining) { omissions.add(label('Some shell commands exceeded the bounded evidence limits.', '部分 Shell 命令超出取证上限。')); continue }
        remaining -= commandBytes
        const output = text(result.data?.message, 8000)
        if (/\b(?:pytest|vitest|tox|test|check|lint|typecheck|tsc|cargo\s+test)\b/i.test(command)) {
          // The model-visible text can contain program output and is not an authoritative exit code.
          events.push({ sessionId: source.sessionId, seq: result.seq, kind: 'check', command, status: 'unknown',
            ...(output ? { details: output } : {}) })
          omissions.add(label('Shell check outcomes require review; model-visible output alone does not prove an exit status.', 'Shell 检查结果需核对；模型可见的输出不能证明退出状态。'))
        } else if (output) observe(result, label(`Shell command: ${command}\nResult (unverified): ${output}`, `Shell 命令：${command}\n结果（未经核实）：${output}`))
      }
    }
    if (!selected) throw new DeliveryError('binding_mismatch')
    if (from > 0 || selectedUnavailable) objective = null
    if (objective === null) omissions.add(label('The original human objective was not available within the bounded session window.', '在取证范围内找不到用户的原始目标。'))
    if (pendingResults.size) omissions.add(label('Some tool results had no matching call in the bounded session window.', '在取证范围内，部分工具结果没有对应的调用。'))
    events.sort((a, b) => b.seq - a.seq)
    return { source, objective, selectedReply: selected, events, workspace: null, omissions: [...omissions] }
  }
}
