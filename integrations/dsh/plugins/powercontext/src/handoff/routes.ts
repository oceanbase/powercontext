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


import { isHandoffLocale } from './locale.ts'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { isAbsolute } from 'node:path'
import { deploymentRejection, type DeploymentFacts } from '../status-bridge.ts'
import type { CallContext, DeliveryService, DeliveryView, PrepareDeliveryInput, SourceBoundary } from './contracts.ts'
import { HandoffError, handoffError } from './errors.ts'
import type { LaunchService } from './launch.ts'
import type { PreflightService } from './preflight.ts'
import type { TargetCatalog } from './targets.ts'
import type { ReceiptReader } from './receipt-contract.ts'
import type { LocalDeliveryService } from './delivery.ts'

const MAX_CONTROL_BYTES = 64 * 1024
const MAX_EXPORT_BYTES = 2 * 1024 * 1024
const BODY_TIMEOUT_MS = 10_000
const PREFIX = '/powercontext'
const TOKEN = /^[A-Za-z0-9._:-]{1,256}$/
const ROUTES = /^(targets|boundaries|preflights|deliveries)(?:\/([A-Za-z0-9._:-]{1,256})(?:\/(install|cancel|export|launch|receipt|pin|delete|diagnose))?)?$/

interface RouteOptions {
  facts: DeploymentFacts
  requireLocal(): void
  requestRejection(request: IncomingMessage): 401 | 403 | undefined
  /** Return a stable owner only after the DSH admission check has passed. */
  authenticatedOwner(request: IncomingMessage): string | undefined
  catalog: TargetCatalog
  signal: AbortSignal
  resolveBoundary?(context: CallContext, sessionId: string, messageId: string): SourceBoundary
  services(): {
    preflight?: PreflightService
    launch?: LaunchService
    delivery?: DeliveryService & Partial<Pick<LocalDeliveryService, 'list' | 'pin' | 'delete'>>
    receipt?: ReceiptReader
  }
}

function json(response: ServerResponse, status: number, value: unknown): void {
  if (response.destroyed || response.writableEnded) return
  response.statusCode = status
  response.setHeader('content-type', 'application/json; charset=utf-8')
  response.end(JSON.stringify(value))
}

function fail(): never { throw new HandoffError('payload_invalid') }

function object(value: unknown, allowed: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || !Object.keys(value).every(key => allowed.includes(key))) return fail()
  return value as Record<string, unknown>
}

function token(value: unknown): string {
  if (typeof value !== 'string' || !TOKEN.test(value)) return fail()
  return value
}

function int(value: unknown, min: number): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min) return fail()
  return value
}

function source(value: unknown): SourceBoundary {
  const { sessionId, messageId, throughSeq, cwd, workspaceObservedAt } =
    object(value, ['sessionId', 'messageId', 'throughSeq', 'cwd', 'workspaceObservedAt'])
  if (typeof cwd !== 'string' || cwd.length > 4096 || !isAbsolute(cwd) || /[\x00-\x1f\x7f]/u.test(cwd)
    || typeof workspaceObservedAt !== 'string' || workspaceObservedAt.length > 64
    || !Number.isFinite(Date.parse(workspaceObservedAt))) return fail()
  return { sessionId: token(sessionId), messageId: token(messageId), throughSeq: int(throughSeq, 0), cwd, workspaceObservedAt }
}

function routeBody(request: IncomingMessage, signal: AbortSignal): Promise<unknown> {
  if (request.headers['content-type']?.split(';', 1)[0]?.trim().toLowerCase() !== 'application/json'
    || request.headers['content-encoding'] !== undefined
    || Number(request.headers['content-length']) > MAX_CONTROL_BYTES) {
    request.resume()
    return Promise.reject(new HandoffError('payload_invalid'))
  }
  const lifetime = AbortSignal.any([signal, AbortSignal.timeout(BODY_TIMEOUT_MS)])
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    let size = 0
    const cleanup = () => {
      request.off('data', data).off('end', end).off('error', stop).off('close', stop)
      lifetime.removeEventListener('abort', stop)
    }
    const stop = () => {
      cleanup()
      request.resume()
      reject(new HandoffError('payload_invalid'))
    }
    const data = (chunk: Buffer) => {
      size += chunk.length
      if (size > MAX_CONTROL_BYTES) stop()
      else chunks.push(chunk)
    }
    const end = () => {
      cleanup()
      try { resolve(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))) }
      catch { reject(new HandoffError('payload_invalid')) }
    }
    if (lifetime.aborted) return stop()
    request.on('data', data).once('end', end).once('error', stop).once('close', stop)
    lifetime.addEventListener('abort', stop, { once: true })
  })
}

function requireWriteOrigin(request: IncomingMessage, desktop: boolean): void {
  const site = request.headers['sec-fetch-site']
  const origin = request.headers.origin
  if (site !== undefined && site !== 'same-origin') throw new HandoffError('deployment_unsupported')
  // Desktop's authenticated forwarder strips the original Origin/Fetch Metadata.
  if (origin === undefined || (desktop && origin === 'dsh-app://app')) return
  const url = URL.canParse(origin) ? new URL(origin) : null
  if (!url || (url.protocol !== 'http:' && url.protocol !== 'https:') || url.origin !== origin
    || url.host !== request.headers.host) throw new HandoffError('deployment_unsupported')
}

function requireService<T>(service: T | undefined): T {
  if (service === undefined) throw new HandoffError('integration_unavailable')
  return service
}

function sameSession(delivery: DeliveryView, sessionId: string): DeliveryView {
  if (delivery.source.sessionId !== sessionId) throw new HandoffError('not_found')
  return delivery
}

export function createHandoffHandler(options: RouteOptions) {
  return async (request: IncomingMessage, response: ServerResponse): Promise<void> => {
    response.setHeader('cache-control', 'no-store')
    response.setHeader('x-content-type-options', 'nosniff')
    const controller = new AbortController()
    const close = () => { if (!response.writableFinished) controller.abort() }
    response.once('close', close)
    const signal = AbortSignal.any([controller.signal, options.signal])
    try {
      const rejection = options.requestRejection(request)
      if (rejection !== undefined) return json(response, rejection, {
        code: rejection === 401 ? 'dsh_authentication_required' : 'dsh_forbidden',
      })
      if (deploymentRejection(request, options.facts)
        || ['forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'].some(name => request.headers[name] !== undefined)) {
        throw new HandoffError('deployment_unsupported')
      }
      options.requireLocal()
      const ownerId = options.authenticatedOwner(request)
      if (ownerId === undefined) return json(response, 403, { code: 'dsh_forbidden' })
      const context: CallContext = { ownerId, signal }
      const raw = request.url ?? ''
      // Do not normalize encoded paths, dot segments or external URLs into an operation.
      if (!raw.startsWith(PREFIX + '/')) throw new HandoffError('not_found')
      const question = raw.indexOf('?')
      const path = question === -1 ? raw : raw.slice(0, question)
      const route = ROUTES.exec(path.slice(PREFIX.length + 1))
      if (!route) throw new HandoffError('not_found')
      const [, resource, id, action] = route
      if ((resource === 'boundaries' && id) || (resource === 'targets' && id && action !== 'diagnose') ||
        (action === 'diagnose' && resource !== 'targets') || (resource === 'preflights' && action && !['install', 'cancel'].includes(action))
        || (resource === 'deliveries' && action === 'install')) throw new HandoffError('not_found')
      const listing = resource === 'deliveries' && !id && request.method === 'GET'
      const method = listing || ['targets', 'boundaries'].includes(resource) || ['export', 'receipt'].includes(action) || (id && !action) ? 'GET' : 'POST'
      if (request.method !== method) {
        response.setHeader('allow', method)
        return json(response, 405, { code: 'method_not_allowed' })
      }
      if (resource === 'targets') {
        if (question !== -1) return fail()
        signal.throwIfAborted()
        if (id) return json(response, 200, { targetId: id, ...await options.catalog.diagnose(id, signal) })
        return json(response, 200, { targets: options.catalog.list() })
      }
      const query = new URLSearchParams(question === -1 ? '' : raw.slice(question + 1))
      if (resource === 'boundaries') {
        if (query.size !== 2) return fail()
        return json(response, 200, { source: requireService(options.resolveBoundary)(context,
          token(query.get('sessionId')), token(query.get('messageId'))) })
      }
      if (method === 'POST' && question !== -1) return fail()
      const querySession = () => {
        if (query.size !== 1) return fail()
        return token(query.get('sessionId'))
      }
      if (method === 'POST') requireWriteOrigin(request, options.facts.desktop)
      const value = method === 'POST' ? await routeBody(request, signal) : undefined
      signal.throwIfAborted()

      if (resource === 'preflights') {
        if (!id) {
          const body = object(value, ['requestId', 'source', 'targetId'])
          const input = { requestId: token(body.requestId), source: source(body.source), targetId: token(body.targetId) }
          options.catalog.require(input.targetId)
          return json(response, 200, await requireService(options.services().preflight).create(context, input))
        }
        const preflight = requireService(options.services().preflight)
        if (!action) return json(response, 200, preflight.get(context, id, querySession()))
        if (action === 'cancel') return json(response, 200,
          preflight.cancel(context, id, token(object(value, ['sessionId']).sessionId)))
        const body = object(value, ['sessionId', 'expectedRevision', 'consent', 'verify', 'confirmOriginal'])
        if (body.confirmOriginal !== undefined && (body.verify !== true || body.confirmOriginal !== true)) return fail()
        const decision = typeof body.consent === 'boolean' && body.verify === undefined
          ? body.consent ? 'install' : 'decline'
          : body.verify === true && body.consent === undefined ? 'verify' : fail()
        return json(response, 200, await preflight.decide(context, id, token(body.sessionId), {
          action: decision, expectedRevision: int(body.expectedRevision, 1), confirmOriginal: body.confirmOriginal === true,
        }))
      }

      const aggregate = (item: DeliveryView, services: ReturnType<RouteOptions['services']>) => {
        let preflight = null
        try { preflight = services.preflight?.get(context, item.binding.preflightId, item.source.sessionId) ?? null }
        catch (error) { if (handoffError(error).code !== 'not_found') throw error }
        return {
          preflight, delivery: item,
          launchAttempts: services.launch?.list(context, item.deliveryId, item.source.sessionId) ?? [],
        }
      }
      if (listing) {
        const sessionId = querySession()
        const delivery = requireService(options.services().delivery)
        const items = await requireService(delivery.list).call(delivery, context)
        return json(response, 200, { deliveries: items.filter(item => item.source.sessionId === sessionId) })
      }
      if (!id) {
        const body = object(value, ['requestId', 'preflightId', 'expectedRevision', 'source', 'workInput', 'revision',
          'supersedesDeliveryId', 'locale'])
        if (body.locale !== undefined && !isHandoffLocale(body.locale)) throw new HandoffError('payload_invalid')
        const input: PrepareDeliveryInput = { requestId: token(body.requestId), preflightId: token(body.preflightId),
          expectedRevision: int(body.expectedRevision, 1), source: source(body.source),
          ...(body.locale === undefined ? {} : { locale: body.locale }),
          // The delivery service owns Handoff field and exact reference validation.
          ...(body.workInput === undefined ? {} : { workInput: body.workInput }),
          ...(body.revision === undefined ? {} : { revision: body.revision as PrepareDeliveryInput['revision'] }),
          ...(body.supersedesDeliveryId === undefined ? {} : { supersedesDeliveryId: token(body.supersedesDeliveryId) }),
        }
        const services = options.services()
        return json(response, 200, aggregate(await requireService(services.delivery).prepare(context, input), services))
      }
      if (action === 'launch') {
        const body = object(value, ['sessionId', 'requestId', 'targetId', 'retryOf', 'locale'])
        if (body.locale !== undefined && !isHandoffLocale(body.locale)) throw new HandoffError('payload_invalid')
        const input = { sessionId: token(body.sessionId), requestId: token(body.requestId), targetId: token(body.targetId),
          ...(body.locale === undefined ? {} : { locale: body.locale }),
          ...(body.retryOf === undefined ? {} : { retryOf: token(body.retryOf) }) }
        return json(response, 200, await requireService(options.services().launch).launch(context, id, input))
      }
      const mutation = method === 'GET' ? undefined : object(value, action === 'pin' ? ['sessionId', 'pinned'] : ['sessionId'])
      const sessionId = method === 'GET' ? querySession() : token(mutation?.sessionId)
      const services = options.services()
      const delivery = requireService(services.delivery)
      const item = sameSession(await delivery.get(context, id), sessionId)
      if (action === 'pin') {
        if (typeof mutation?.pinned !== 'boolean') return fail()
        return json(response, 200, await requireService(delivery.pin).call(delivery, context, id, mutation.pinned))
      }
      if (action === 'delete') {
        await requireService(delivery.delete).call(delivery, context, id)
        return json(response, 200, { deleted: true })
      }
      if (action === 'receipt') {
        if (item.expired) {
          return json(response, 200, await requireService(services.receipt).saved(context, item) ?? {
            deliveryId: id, state: 'awaiting', readState: 'stale', observedAt: new Date().toISOString(),
          })
        }
        const bundle = await delivery.getForLaunch(context, id)
        return json(response, 200, await requireService(services.receipt).read(context, bundle))
      }
      if (action === 'cancel') {
        const [prepared, launch] = await Promise.allSettled([
          Promise.resolve().then(() => delivery.cancelPreparation(context, id)),
          Promise.resolve().then(() => services.launch?.cancelPending(context, id, sessionId)),
        ])
        if (prepared.status === 'rejected') throw prepared.reason
        if (launch.status === 'rejected') throw launch.reason
        return json(response, 200, aggregate(prepared.value, services))
      }
      if (action === 'export') {
        const { bytes } = await delivery.exportPackage(context, id)
        if (!(bytes instanceof Uint8Array) || bytes.length === 0 || bytes.length > MAX_EXPORT_BYTES) return fail()
        response.setHeader('content-type', 'application/json')
        response.setHeader('content-disposition', 'attachment; filename="powercontext-handoff.json"')
        response.setHeader('content-length', bytes.length)
        response.end(bytes)
        return
      }
      return json(response, 200, aggregate(item, services))
    } catch (error) {
      const safe = signal.aborted ? new HandoffError('cancelled') : handoffError(error)
      json(response, safe.status, { code: safe.code, message: safe.message })
    } finally {
      response.off('close', close)
    }
  }
}
