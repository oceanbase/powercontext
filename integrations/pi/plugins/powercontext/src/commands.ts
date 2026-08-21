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

import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import {
  confirmDurableWrite,
  invokeOperation,
  invokeScopedOperation,
  unavailableResult,
  type ToolResult,
} from './invoke.ts'
import type { PluginRuntime } from './recall.ts'

type CommandContext = {
  cwd: string
  hasUI: boolean
  signal?: AbortSignal
  ui: {
    confirm: (title: string, message: string) => Promise<boolean>
    notify: (message: string, level: 'info' | 'error') => void
  }
}

function format(result: ToolResult | Record<string, unknown>): string {
  return JSON.stringify(result, null, 2)
}

function report(context: CommandContext, message: string, isError = false): void {
  if (context.hasUI) {
    context.ui.notify(message, isError ? 'error' : 'info')
    return
  }
  console.log(message)
}

async function handleRemember(runtime: PluginRuntime, context: CommandContext, text: string): Promise<ToolResult> {
  if (!text) return { ok: false, code: 'invalid_request', message: 'Usage: /pc remember <text>' }
  const confirmation = await confirmDurableWrite(context, 'remember operation')
  if (confirmation) return confirmation
  return invokeScopedOperation(runtime, context, 'remember_memory', { kind: 'agent-note', text })
}

export async function handlePcCommand(
  rawInput: string,
  runtime: PluginRuntime,
  context: CommandContext,
): Promise<void> {
  const tokens = rawInput.trim().split(/\s+/).filter(Boolean)
  const command = tokens[0]
  if (!command) {
    try {
      const scopeId = await runtime.resolveScope(context.cwd)
      report(context, `scope=${scopeId}\nbaseUrl=${runtime.config.baseUrl}\nUse /pc doctor to check Server readiness.`)
    } catch {
      report(context, format(unavailableResult()), true)
    }
    return
  }
  if (command === 'doctor') {
    try {
      const scopeId = await runtime.resolveScope(context.cwd)
      const [live, ready] = await Promise.all([
        invokeOperation(runtime.client, 'get_liveness', {}, scopeId, context.signal),
        invokeOperation(runtime.client, 'get_readiness', {}, scopeId, context.signal),
      ])
      report(context, format({ ok: live.ok && ready.ok, live, ready }), !live.ok || !ready.ok)
    } catch {
      report(context, format(unavailableResult()), true)
    }
    return
  }
  if (command === 'search') {
    const query = tokens.slice(1).join(' ')
    const result = query
      ? await invokeScopedOperation(runtime, context, 'search_memory', { query, limit: 8, mode: 'auto' })
      : { ok: false, code: 'invalid_request', message: 'Usage: /pc search <query>' }
    report(context, format(result), !result.ok)
    return
  }
  if (command === 'remember') {
    const result = await handleRemember(runtime, context, tokens.slice(1).join(' '))
    report(context, format(result), !result.ok)
    return
  }
  if (command === 'flush') {
    const confirmation = await confirmDurableWrite(context, 'flush operation')
    const result = confirmation ?? await invokeScopedOperation(runtime, context, 'flush_memory', {})
    report(context, format(result), !result.ok)
    return
  }
  if (command === 'stats') {
    const result = await invokeScopedOperation(runtime, context, 'get_stats', {})
    report(context, format(result), !result.ok)
    return
  }
  report(
    context,
    'Unknown /pc subcommand. Try doctor, search, remember, flush, or stats.',
    true,
  )
}

export function registerCommands(pi: ExtensionAPI, runtime: PluginRuntime): void {
  pi.registerCommand('pc', {
    description: 'PowerContext status, search, and diagnostics',
    handler: async (args, context) => {
      await handlePcCommand(args, runtime, context)
    },
  })
}
