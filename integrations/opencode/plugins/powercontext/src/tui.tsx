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

import type { TuiPlugin, TuiPluginApi, TuiPluginModule } from '@opencode-ai/plugin/tui'
import { createElement, insert, setProp } from '@opentui/solid'
import { createSignal, onCleanup } from 'solid-js'

import { combineSignals, PowerContextClient } from './client.ts'
import { PC_COMMAND_USAGE, handlePcCommand, type PcCommandResult, type PcCommandRuntime } from './commands.ts'
import { resolveConfig } from './config.ts'
import { PLUGIN_NAME, ServerResponseError, UnavailableError } from './errors.ts'
import { invokeOperation, type ToolResult } from './invoke.ts'
import { resolveScopeId } from './scope.ts'

const COMMAND_NAME = 'powercontext.pc'
const STATUS_REFRESH_MS = 30_000
const STATUS_TIMEOUT_MS = 3_000

type TokenTotals = {
  preparations: number
  ready_preparations: number
  comparable_preparations: number
  baseline_tokens: number
  recalled_tokens: number
  token_reduction: number
}

type StatuslineState = {
  connected: boolean
  label: string
  todayReduction?: number
  monthReduction?: number
}

function sessionDirectory(api: TuiPluginApi, sessionID?: string): string | undefined {
  if (sessionID) {
    const directory = api.state.session.get(sessionID)?.directory?.trim()
    if (directory) return directory
  }
  return api.state.path.directory?.trim() || undefined
}

function currentSessionID(api: TuiPluginApi): string | undefined {
  const route = api.route.current
  if (
    route.name === 'session'
    && 'params' in route
    && route.params
    && typeof route.params.sessionID === 'string'
  ) return route.params.sessionID
  return undefined
}

function currentDirectory(api: TuiPluginApi): string | undefined {
  return sessionDirectory(api, currentSessionID(api))
}

function compactTokens(value: number): string {
  const absolute = Math.abs(value)
  const format = (scaled: number, suffix: string) => {
    const digits = scaled < 10 ? 1 : 0
    return `${scaled.toFixed(digits).replace(/\.0$/, '')}${suffix}`
  }
  if (absolute >= 1_000_000) return format(value / 1_000_000, 'm')
  if (absolute >= 1_000) return format(value / 1_000, 'k')
  return String(value)
}

function tokenTotals(value: unknown): TokenTotals | undefined {
  if (!value || typeof value !== 'object') return undefined
  const recall = (value as { recall?: unknown }).recall
  if (!recall || typeof recall !== 'object') return undefined
  const totals = (recall as { totals?: unknown }).totals
  if (!totals || typeof totals !== 'object') return undefined
  const candidate = totals as Record<string, unknown>
  const preparations = candidate.preparations
  const ready = candidate.ready_preparations
  const comparable = candidate.comparable_preparations
  const baseline = candidate.baseline_tokens
  const recalled = candidate.recalled_tokens
  const reduction = candidate.token_reduction
  if (
    !Number.isInteger(preparations) || Number(preparations) < 0
    || !Number.isInteger(ready) || Number(ready) < 0
    || !Number.isInteger(comparable) || Number(comparable) < 0
    || !Number.isInteger(baseline) || Number(baseline) < 0
    || !Number.isInteger(recalled) || Number(recalled) < 0
    || !Number.isInteger(reduction)
  ) return undefined
  return {
    preparations: Number(preparations),
    ready_preparations: Number(ready),
    comparable_preparations: Number(comparable),
    baseline_tokens: Number(baseline),
    recalled_tokens: Number(recalled),
    token_reduction: Number(reduction),
  }
}

function reductionOf(value: unknown): number | undefined {
  return tokenTotals(value)?.token_reduction
}

function savingsPhrase(reduction: number | undefined, suffix: string): string {
  if (reduction === undefined) return `no data ${suffix}`
  const amount = compactTokens(Math.abs(reduction))
  return `${reduction >= 0 ? 'saved' : 'cost'} ${amount} ${suffix}`
}

function savingsColor(reduction: number | undefined, api: TuiPluginApi): unknown {
  if (reduction === undefined || reduction === 0) return api.theme.current.textMuted
  return reduction > 0 ? api.theme.current.success : api.theme.current.error
}

export function formatPowerContextStatus(today: unknown, month: unknown): string {
  return `PC online · ${savingsPhrase(reductionOf(today), 'today')} · ${savingsPhrase(reductionOf(month), 'in 30d')}`
}

export type FailureOutcome = 'authentication_failed' | 'version_mismatch' | 'server_unavailable' | 'invalid_response'

const FAILURE_LABELS: Record<FailureOutcome, string> = {
  authentication_failed: 'PC auth failed',
  version_mismatch: 'PC version mismatch',
  server_unavailable: 'PC offline · run powercontext doctor',
  invalid_response: 'PC invalid response',
}

export function failureLabel(outcome: FailureOutcome): string {
  return FAILURE_LABELS[outcome]
}

class StatusTimeoutError extends Error {
  constructor() {
    super('PowerContext status timed out')
    this.name = 'StatusTimeoutError'
  }
}

function httpOutcome(status: number): FailureOutcome {
  if (status === 401) return 'authentication_failed'
  if (status === 404) return 'version_mismatch'
  if (status === 503) return 'server_unavailable'
  return 'invalid_response'
}

function scopeFailureOutcome(error: unknown): FailureOutcome {
  if (error instanceof StatusTimeoutError) return 'server_unavailable'
  if (error instanceof ServerResponseError) return httpOutcome(error.statusCode)
  if (error instanceof UnavailableError) return 'server_unavailable'
  return 'invalid_response'
}

function statsFailureOutcome(result: ToolResult): FailureOutcome {
  if (result.code === 'authentication_failed') return 'authentication_failed'
  if (result.code === 'unavailable') return 'server_unavailable'
  if (typeof result.status === 'number') return httpOutcome(result.status)
  return 'invalid_response'
}

function textNode(text: string, color: unknown, onMouseUp?: () => void): any {
  const node = createElement('text')
  setProp(node, 'fg', color)
  if (onMouseUp) setProp(node, 'onMouseUp', onMouseUp)
  insert(node, text)
  return node
}

export function withTimeout<Value>(promise: Promise<Value>, timeoutMs: number): Promise<Value> {
  return new Promise<Value>((resolve, reject) => {
    const timer = setTimeout(() => reject(new StatusTimeoutError()), timeoutMs)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

export async function loadStatuslineStatus(
  runtime: PcCommandRuntime,
  sessionID: string,
  cwd: string | undefined,
  signal?: AbortSignal,
): Promise<StatuslineState> {
  if (!cwd && !runtime.config.scopeId) return { connected: false, label: 'PC unavailable' }
  let scopeId: string
  try {
    scopeId = await withTimeout(
      resolveScopeId(runtime.client, {
        cwd,
        sessionID,
        configuredScopeId: runtime.config.scopeId,
      }, signal),
      STATUS_TIMEOUT_MS,
    )
  } catch (error) {
    return { connected: false, label: failureLabel(scopeFailureOutcome(error)) }
  }
  try {
    const [today, month] = await Promise.all([
      withTimeout(invokeOperation(runtime.client, 'get_stats', { period: 'today' }, scopeId, signal), STATUS_TIMEOUT_MS),
      withTimeout(invokeOperation(runtime.client, 'get_stats', { period: '30d' }, scopeId, signal), STATUS_TIMEOUT_MS),
    ])
    if (!today.ok) return { connected: false, label: failureLabel(statsFailureOutcome(today)) }
    if (!month.ok) return { connected: false, label: failureLabel(statsFailureOutcome(month)) }
    const todayTotals = tokenTotals(today.data)
    const monthTotals = tokenTotals(month.data)
    if (!todayTotals || !monthTotals) return { connected: false, label: failureLabel('invalid_response') }
    return {
      connected: true,
      label: formatPowerContextStatus(today.data, month.data),
      todayReduction: todayTotals.token_reduction,
      monthReduction: monthTotals.token_reduction,
    }
  } catch (error) {
    return { connected: false, label: failureLabel(scopeFailureOutcome(error)) }
  }
}

function tokenSavingsView(api: TuiPluginApi, runtime: PcCommandRuntime, sessionID: string): any {
  const [state, setState] = createSignal<StatuslineState>({ connected: false, label: 'PC offline' })
  const controller = new AbortController()
  let disposed = false
  const root = createElement('box')
  setProp(root, 'flexDirection', 'row')
  setProp(root, 'gap', 1)
  setProp(root, 'alignItems', 'center')

  const loadStatus = (): Promise<StatuslineState> =>
    loadStatuslineStatus(
      runtime,
      sessionID,
      sessionDirectory(api, sessionID),
      combineSignals([api.lifecycle.signal, controller.signal]),
    )

  const refresh = () => {
    void withTimeout(loadStatus(), STATUS_TIMEOUT_MS * 2 + 1_000).then(
      (value) => {
        if (!disposed) setState(value)
      },
      () => {
        if (!disposed) setState({ connected: false, label: failureLabel('server_unavailable') })
      },
    )
  }

  insert(root, () => {
    const current = state()
    const connectionColor = current.connected ? api.theme.current.success : api.theme.current.error
    if (!current.connected) {
      return [
        textNode('●', connectionColor, () => void refresh()),
        textNode(current.label, api.theme.current.textMuted),
      ]
    }
    return [
      textNode('●', connectionColor, () => void refresh()),
      textNode('PC online · ', api.theme.current.textMuted),
      textNode(savingsPhrase(current.todayReduction, 'today'), savingsColor(current.todayReduction, api)),
      textNode(' · ', api.theme.current.textMuted),
      textNode(savingsPhrase(current.monthReduction, 'in 30d'), savingsColor(current.monthReduction, api)),
    ]
  })
  void refresh()
  const timer = setInterval(() => void refresh(), STATUS_REFRESH_MS)
  // The status view is a component slot: the plugin lifetime outlives it, so the
  // poll and any in-flight request must stop when the view itself is disposed.
  onCleanup(() => {
    disposed = true
    clearInterval(timer)
    controller.abort()
  })
  return root
}

function showResult(api: TuiPluginApi, result: PcCommandResult): void {
  const DialogAlert = api.ui.DialogAlert
  api.ui.dialog.setSize('large')
  api.ui.dialog.replace(() => DialogAlert({
    title: result.kind === 'success' ? 'PowerContext' : 'PowerContext error',
    message: result.text,
    onConfirm: () => api.ui.dialog.clear(),
  }))
}

async function runCommand(api: TuiPluginApi, runtime: PcCommandRuntime, rawInput: string): Promise<void> {
  api.ui.dialog.clear()
  try {
    const cwd = currentDirectory(api)
    if (!cwd && !runtime.config.scopeId) {
      showResult(api, { kind: 'error', text: 'PowerContext could not resolve the current OpenCode project directory.' })
      return
    }
    const scopeId = await resolveScopeId(runtime.client, {
      cwd,
      sessionID: currentSessionID(api),
      configuredScopeId: runtime.config.scopeId,
    }, api.lifecycle.signal)
    showResult(api, await handlePcCommand(rawInput, runtime, scopeId, api.lifecycle.signal))
  } catch {
    showResult(api, { kind: 'error', text: 'PowerContext is unavailable; continue normal work.' })
  }
}

function showCommandPrompt(api: TuiPluginApi, runtime: PcCommandRuntime): void {
  const DialogPrompt = api.ui.DialogPrompt
  api.ui.dialog.setSize('large')
  api.ui.dialog.replace(() => DialogPrompt({
    title: 'PowerContext /pc',
    placeholder: PC_COMMAND_USAGE,
    onConfirm: (value) => void runCommand(api, runtime, value),
    onCancel: () => api.ui.dialog.clear(),
  }))
}

export const PowerContextTuiPlugin: TuiPlugin = async (api) => {
  let config
  try {
    config = resolveConfig()
  } catch (error) {
    api.ui.toast({
      variant: 'error',
      title: 'PowerContext',
      message: `configuration rejected: ${String(error)}`,
    })
    return
  }
  const runtime: PcCommandRuntime = {
    config,
    client: new PowerContextClient({
      baseUrl: config.baseUrl,
      allowInsecureHttp: config.allowInsecureHttp,
      authorization: config.authorization,
      requestTimeoutMs: config.requestTimeoutMs,
    }),
  }
  api.keymap.registerLayer({
    commands: [{
      name: COMMAND_NAME,
      title: 'PowerContext command',
      category: 'PowerContext',
      namespace: 'palette',
      slashName: 'pc',
      slashAliases: ['powercontext'],
      run: () => showCommandPrompt(api, runtime),
    }],
  })
  api.slots.register({
    order: 50,
    slots: {
      session_prompt_right(_context, props) {
        return tokenSavingsView(api, runtime, props.session_id)
      },
    },
  })
}

const plugin = { id: `${PLUGIN_NAME}-tui`, tui: PowerContextTuiPlugin } satisfies TuiPluginModule
export default plugin
