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

import { createHash } from 'node:crypto'
import { appendFileSync } from 'node:fs'

const TOOL_PREFIX = 'mcp__powercontext__'

function isPowerContextSnapshot(message) {
  return message.source?.plugin === 'powercontext-dsh'
    && message.source?.form === 'snapshot'
    && message.source?.sections?.some(section => section.name === 'PowerContext')
}

function sameStep(state, data) {
  return state.turn === data.turn && state.step === data.step
}

export const inject = ['sessions']

export function apply(ctx, config) {
  const states = new Map()
  ctx.on('session/event', (session, event) => {
    const sessionId = String(session.id)
    if (event.type === 'step/start') {
      states.set(sessionId, { turn: event.data.turn, step: event.data.step, snapshotSeq: undefined, calls: new Map() })
      return
    }
    const state = states.get(sessionId)
    if (!state) return
    if (event.type === 'user/message' && isPowerContextSnapshot(event.data)) {
      state.snapshotSeq = event.seq
      return
    }
    if (event.type === 'tool/call' && sameStep(state, event.data) && event.data.name.startsWith(TOOL_PREFIX)) {
      state.calls.set(String(event.data.callId), { name: event.data.name, seq: event.seq })
      return
    }
    if (event.type === 'tool/result' && sameStep(state, event.data)) {
      const callId = String(event.data.message?.source?.callId ?? '')
      const call = state.calls.get(callId)
      if (!call || state.snapshotSeq === undefined) return
      const result = event.data.message.content?.[0]
      appendFileSync(config.traceFile, JSON.stringify({
        schema: 'powercontext.dsh.context-observation.v1',
        session_id_hash: createHash('sha256').update(sessionId).digest('hex'),
        turn_id: state.turn,
        step_id: state.step,
        call_id: callId,
        tool: call.name,
        snapshot_injection: { event_seq: state.snapshotSeq },
        tool_call: { event_seq: call.seq },
        tool_result: { event_seq: event.seq, is_error: result?.isError === true || Boolean(event.data.error) },
      }) + '\n')
      state.calls.delete(callId)
      return
    }
    if (event.type === 'step/end' && sameStep(state, event.data)) states.delete(sessionId)
  })
  ctx.on('session/disposed', session => states.delete(String(session.id)))
}
