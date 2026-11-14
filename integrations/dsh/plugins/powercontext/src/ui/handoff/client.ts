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

import type { HandoffLocale } from '../../handoff/locale.ts'
import type { DeliveryView, SourceBoundary } from '../../handoff/contracts.ts'
import type { PreflightView } from '../../handoff/preflight.ts'
import type { LaunchView } from '../../handoff/launch.ts'
import type { ReceiptView } from '../../handoff/receipt-contract.ts'

export interface Target { targetId: string; name: string; availability: string; action: string; diagnostics?: string }
export type TargetDiagnostic = { targetId: string; state: 'present'; version: string }
  | { targetId: string; state: 'missing' | 'unknown' | 'unsupported' }
export interface DeliverySnapshot { delivery: DeliveryView; preflight: PreflightView | null; launchAttempts: LaunchView[] }
/** Both operations are served by the owner-local Host, after DSH authentication. */
export interface HandoffPorts {
  resolveBoundary(sessionId: string, messageId: string): Promise<SourceBoundary>
  readReceipt: (deliveryId: string, sessionId: string) => Promise<ReceiptView>
}

export class HandoffHttpError extends Error {
  readonly status: number
  constructor(code: string, status: number) { super(code); this.status = status }
}

export class HandoffHttp implements HandoffPorts {
  private readonly fetcher: typeof fetch
  constructor(fetcher: typeof fetch = (input, init) => fetch(input, init)) { this.fetcher = fetcher }
  private async json<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await this.fetcher('/powercontext/' + path, body === undefined ? { cache: 'no-store', signal } : {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store', signal,
    })
    if (!response.ok) {
      const error = await response.json().catch(() => ({})) as { code?: unknown }
      throw new HandoffHttpError(typeof error.code === 'string' ? error.code : 'integration_unavailable', response.status)
    }
    return response.json() as Promise<T>
  }
  async resolveBoundary(sessionId: string, messageId: string): Promise<SourceBoundary> {
    const response = await this.json<{ source?: SourceBoundary }>(
      `boundaries?sessionId=${encodeURIComponent(sessionId)}&messageId=${encodeURIComponent(messageId)}`)
    const source = response?.source
    if (!source || source.sessionId !== sessionId || source.messageId !== messageId
      || !Number.isSafeInteger(source.throughSeq) || source.throughSeq < 0
      || typeof source.cwd !== 'string' || !source.cwd || !Number.isFinite(Date.parse(source.workspaceObservedAt))) {
      throw new HandoffHttpError('binding_mismatch', 409)
    }
    return Object.freeze({ ...source })
  }
  async readReceipt(deliveryId: string, sessionId: string): Promise<ReceiptView> {
    const result = await this.json<ReceiptView>(`deliveries/${encodeURIComponent(deliveryId)}/receipt?sessionId=${encodeURIComponent(sessionId)}`)
    if (!result || result.deliveryId !== deliveryId || !['awaiting', 'accepted', 'needs_clarification', 'declined'].includes(result.state)
      || !['not_found', 'verified', 'unavailable', 'forbidden', 'invalid', 'stale'].includes(result.readState)
      || typeof result.observedAt !== 'string' || !Number.isFinite(Date.parse(result.observedAt))) {
      throw new HandoffHttpError('receipt_invalid', 422)
    }
    const attribution = result.attribution
    if (attribution && (typeof attribution.receiver !== 'string' || !attribution.receiver.trim()
      || (attribution.kind !== 'self_reported' && attribution.kind !== 'authenticated')
      || (attribution.kind === 'authenticated' && (!attribution.principal
        || typeof attribution.principal.type !== 'string' || typeof attribution.principal.id !== 'string')))) {
      throw new HandoffHttpError('receipt_invalid', 422)
    }
    return result
  }
  targets() { return this.json<{ targets: Target[] }>('targets') }
  async diagnoseTarget(targetId: string, signal?: AbortSignal): Promise<TargetDiagnostic> {
    const result = await this.json<TargetDiagnostic>(`targets/${encodeURIComponent(targetId)}/diagnose`, undefined, signal)
    if (!result || result.targetId !== targetId || !['present', 'missing', 'unknown', 'unsupported'].includes(result.state)
      || (result.state === 'present' && (typeof result.version !== 'string' || !result.version.trim()))) {
      throw new HandoffHttpError('integration_unavailable', 503)
    }
    return result
  }
  preflight(source: SourceBoundary, targetId: string, requestId: string, signal?: AbortSignal) {
    return this.json<PreflightView>('preflights', { source, targetId, requestId }, signal)
  }
  async getPreflight(preflightId: string, sessionId: string, signal?: AbortSignal) {
    const result = await this.json<PreflightView>(`preflights/${encodeURIComponent(preflightId)}?sessionId=${encodeURIComponent(sessionId)}`, undefined, signal)
    if (result?.preflightId !== preflightId || result.source?.sessionId !== sessionId) throw new HandoffHttpError('binding_mismatch', 409)
    return result
  }
  decision(preflight: PreflightView, consent: boolean, signal?: AbortSignal) {
    return this.json<PreflightView>(`preflights/${encodeURIComponent(preflight.preflightId)}/install`, {
      sessionId: preflight.source.sessionId, expectedRevision: preflight.revision, consent,
    }, signal)
  }
  verify(preflight: PreflightView, confirmOriginal = false, signal?: AbortSignal) {
    return this.json<PreflightView>(`preflights/${encodeURIComponent(preflight.preflightId)}/install`, {
      sessionId: preflight.source.sessionId, expectedRevision: preflight.revision, verify: true,
      ...(confirmOriginal ? { confirmOriginal: true } : {}),
    }, signal)
  }
  cancelPreflight(preflight: PreflightView, signal?: AbortSignal) {
    return this.json<PreflightView>(`preflights/${encodeURIComponent(preflight.preflightId)}/cancel`, { sessionId: preflight.source.sessionId }, signal)
  }
  prepare(preflight: PreflightView, requestId: string, workInput?: unknown, supersedesDeliveryId?: string, signal?: AbortSignal, locale?: HandoffLocale) {
    return this.json<DeliverySnapshot>('deliveries', { preflightId: preflight.preflightId,
      expectedRevision: preflight.revision, source: preflight.source, requestId,
      ...(locale === undefined ? {} : { locale }), ...(workInput === undefined ? {} : { workInput }), ...(supersedesDeliveryId ? { supersedesDeliveryId } : {}) }, signal)
  }
  async history(sessionId: string, signal?: AbortSignal) {
    const result = await this.json<{ deliveries: DeliveryView[] }>(`deliveries?sessionId=${encodeURIComponent(sessionId)}`, undefined, signal)
    if (!Array.isArray(result?.deliveries) || result.deliveries.some(item => item.source?.sessionId !== sessionId)) {
      throw new HandoffHttpError('binding_mismatch', 409)
    }
    return result
  }
  async pin(deliveryId: string, sessionId: string, pinned: boolean, signal?: AbortSignal) {
    const result = await this.json<DeliveryView>(`deliveries/${encodeURIComponent(deliveryId)}/pin`, { sessionId, pinned }, signal)
    if (result?.deliveryId !== deliveryId || result.source?.sessionId !== sessionId || result.pinned !== pinned) {
      throw new HandoffHttpError('binding_mismatch', 409)
    }
    return result
  }
  async delete(deliveryId: string, sessionId: string, signal?: AbortSignal) {
    const result = await this.json<{ deleted: boolean }>(`deliveries/${encodeURIComponent(deliveryId)}/delete`, { sessionId }, signal)
    if (result?.deleted !== true) throw new HandoffHttpError('integration_unavailable', 503)
    return result
  }
  async get(deliveryId: string, sessionId: string, signal?: AbortSignal) {
    const result = await this.json<DeliverySnapshot>(`deliveries/${encodeURIComponent(deliveryId)}?sessionId=${encodeURIComponent(sessionId)}`, undefined, signal)
    if (result?.delivery?.deliveryId !== deliveryId || result.delivery.source?.sessionId !== sessionId) throw new HandoffHttpError('binding_mismatch', 409)
    return result
  }
  cancel(deliveryId: string, sessionId: string, signal?: AbortSignal) {
    return this.json<DeliverySnapshot>(`deliveries/${encodeURIComponent(deliveryId)}/cancel`, { sessionId }, signal)
  }
  launch(deliveryId: string, sessionId: string, targetId: string, requestId: string, retryOf?: string, signal?: AbortSignal, locale?: HandoffLocale) {
    return this.json<LaunchView>(`deliveries/${encodeURIComponent(deliveryId)}/launch`, {
      sessionId, targetId, requestId, ...(locale === undefined ? {} : { locale }), ...(retryOf ? { retryOf } : {}),
    }, signal)
  }
  exportUrl(deliveryId: string, sessionId: string) {
    return `/powercontext/deliveries/${encodeURIComponent(deliveryId)}/export?sessionId=${encodeURIComponent(sessionId)}`
  }
}

export function workInput(objective: string, state: string, next: string, omissions: string, disposition: 'continuable' | 'blocked' | 'complete', locale: HandoffLocale = 'en') {
  return { schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input', objective: objective.trim(),
    state: [{ text: state.trim(), basis: 'declared', evidence: [] }], disposition,
    next_action: next.trim() ? { text: next.trim(), basis: 'declared', evidence: [] } : null,
    omissions: [...new Set([...omissions.split('\n').map(line => line.trim()).filter(Boolean),
      locale === 'zh-CN' ? '未取得历史工作区快照。' : 'The historical workspace observation was not available.'])] }
}
