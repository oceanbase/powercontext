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

import {
  parseStatusPayload, STATUS_CONNECTING_RETRY_MS, STATUS_REFRESH_INTERVAL_MS, STATUS_REQUEST_TIMEOUT_MS,
  STATUS_RETRY_DELAYS_MS, STATUS_ROUTE, STATUS_STALE_AFTER_MS,
  type DisplayState, type StatusPayload,
} from '../status-contract.ts'

export type FetchLike = (input: string | URL, init?: RequestInit) => Promise<Response>

export interface StatusError {
  kind: 'authentication' | 'forbidden' | 'bridge' | 'network' | 'protocol' | 'unavailable'
  code: string
  message: string
  recovery?: string
  http_status?: number
}

export interface StatusSnapshot {
  /** Display state derived at the last publication. */
  state: DisplayState
  /** A request is in flight; existing data stays visible. */
  loading: boolean
  data: StatusPayload | null
  error: StatusError | null
  last_success_at: number | null
  session_id: string | null
  generation: number
}

export interface StatusControllerOptions {
  fetch?: FetchLike
  now?: () => number
  document?: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'> | null
  setTimeout?: typeof setTimeout
  clearTimeout?: typeof clearTimeout
  /** Prefix for the status route; tests use an absolute origin, the shipped UI uses ''. */
  baseUrl?: string
}

/** Reduce a Session list snapshot to the conversation currently retained by the main view. */
export function activeSessionId(state: {
  byId: Record<string, { id?: string; retainedBy?: { mainView?: number } }>
}): string | undefined {
  return Object.values(state.byId)
    .find(session => (session.retainedBy?.mainView ?? 0) > 0)?.id
}

/** Map a snapshot to the four-state sidebar palette, treating an expired success as retrying. */
export function displayState(snapshot: StatusSnapshot, now: number): DisplayState {
  if (snapshot.error !== null) {
    return snapshot.error.kind === 'bridge'
      && ['host_bridge_unavailable', 'owner_local_unverified', 'owner_local_disabled', 'ssh_untrusted']
        .includes(snapshot.error.code)
      ? 'unconfigured' : 'failed'
  }
  if (snapshot.data === null) return snapshot.loading ? 'connecting' : 'unconfigured'
  const connection = snapshot.data.connection
  if (connection.state === 'failed') return 'failed'
  if (connection.state === 'unconfigured') return 'unconfigured'
  if (connection.state === 'ready') {
    const lastSuccess = snapshot.last_success_at ?? 0
    return connection.stale || now - lastSuccess >= STATUS_STALE_AFTER_MS ? 'stale' : 'ready'
  }
  return 'connecting'
}

function initialSnapshot(): StatusSnapshot {
  return {
    state: 'connecting',
    loading: false,
    data: null,
    error: null,
    last_success_at: null,
    session_id: null,
    generation: 0,
  }
}

function normalizeSession(sessionId: string | undefined): string | undefined {
  const value = sessionId?.trim()
  return value ? value.slice(0, 256) : undefined
}

function errorFromResponse(response: Response, body: Record<string, unknown> | null): StatusError {
  const code = typeof body?.code === 'string' ? body.code.slice(0, 128) : undefined
  const message = typeof body?.message === 'string' ? body.message.slice(0, 1_024) : undefined
  const recovery = typeof body?.recovery === 'string' ? body.recovery.slice(0, 1_024) : undefined
  if (response.status === 401) {
    return {
      kind: 'authentication', code: code ?? 'plugin_authentication_required', http_status: 401,
      message: message ?? 'DSH authentication is required before PowerContext status can be read.',
      recovery: recovery ?? 'Sign in to DSH again, then retry.',
    }
  }
  if (response.status === 403) {
    if (code === 'owner_local_unverified' || code === 'owner_local_disabled' || code === 'ssh_untrusted') {
      return {
        kind: 'bridge', code, http_status: 403,
        message: message ?? 'This host is not a verified local DSH owner deployment.',
        recovery: recovery ?? 'Configure ownerLocal only for your own local Web host, or use DSH Desktop.',
      }
    }
    return {
      kind: 'forbidden', code: code ?? 'plugin_forbidden', http_status: 403,
      message: message ?? 'This DSH request is not allowed to read PowerContext status.',
      recovery: recovery ?? 'Use the local DSH owner session; remote or shared hosts are not supported.',
    }
  }
  if (response.status === 404) {
    return {
      kind: 'bridge', code: 'host_bridge_unavailable', http_status: 404,
      message: 'This host does not expose the PowerContext status bridge.',
      recovery: 'Update the PowerContext DSH plugin and restart the host.',
    }
  }
  return {
    kind: 'bridge', code: code ?? 'status_bridge_error', http_status: response.status,
    message: message ?? `The PowerContext status bridge returned HTTP ${String(response.status)}.`,
    recovery: recovery ?? 'Check DSH logs and the installed plugin build.',
  }
}

async function readErrorBody(response: Response): Promise<Record<string, unknown> | null> {
  try {
    const text = (await response.text()).slice(0, 4_096)
    const parsed: unknown = JSON.parse(text)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown> : null
  } catch {
    // Status bodies are small JSON objects; anything else is not a usable code.
    return null
  }
}

/**
 * Visibility-aware status refresher. It owns one request at a time, invalidates
 * responses when the Session or a manual refresh supersedes them, pauses while
 * hidden, and backs off after failures. It never reads `/pc doctor`.
 */
export class StatusController {
  private readonly fetcher: FetchLike
  private readonly now: () => number
  private readonly visibility: StatusControllerOptions['document']
  private readonly setTimer: typeof setTimeout
  private readonly clearTimer: typeof clearTimeout
  private readonly baseUrl: string
  private readonly listeners = new Set<() => void>()
  private snapshot: StatusSnapshot = initialSnapshot()
  private started = false
  private visible = true
  private inFlight = false
  private retry = 0
  private generation = 0
  private timer: ReturnType<typeof setTimeout> | undefined
  private abort: AbortController | undefined

  constructor(options: StatusControllerOptions = {}) {
    this.fetcher = options.fetch ?? ((input, init) => fetch(input, init))
    this.now = options.now ?? Date.now
    this.visibility = options.document === undefined
      ? (typeof document === 'undefined' ? null : document)
      : options.document
    // Browser timers are Window methods and throw "Illegal invocation" when
    // detached and called with an arbitrary receiver. Bind them through local
    // wrappers instead of storing the host functions on this instance.
    this.setTimer = options.setTimeout ?? ((handler, milliseconds) => setTimeout(handler, milliseconds))
    this.clearTimer = options.clearTimeout ?? (timer => clearTimeout(timer))
    this.baseUrl = (options.baseUrl ?? '').replace(/\/+$/, '')
    this.visible = this.visibility?.visibilityState !== 'hidden'
  }

  readonly getSnapshot = (): StatusSnapshot => this.snapshot

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    if (!this.started) this.start()
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) this.stop()
    }
  }

  /** Attach visibility tracking and make the first bounded request. */
  start(): void {
    if (this.started) return
    this.started = true
    this.visibility?.addEventListener('visibilitychange', this.onVisibility)
    this.visible = this.visibility?.visibilityState !== 'hidden'
    if (this.visible) this.refreshNow()
  }

  /** Detach timers and listeners; retained data stays available until the next mount. */
  stop(): void {
    if (!this.started) return
    this.started = false
    this.visibility?.removeEventListener('visibilitychange', this.onVisibility)
    this.cancelScheduled()
    this.abort?.abort()
    this.abort = undefined
    this.inFlight = false
    this.generation += 1
  }

  /** Release everything; called from the plugin effect's disposer. */
  dispose(): void {
    this.stop()
    this.listeners.clear()
  }

  /**
   * Select the active conversation. A changed identity invalidates old data and
   * supersedes any response already in flight.
   */
  setSession(sessionId: string | undefined): void {
    const next = normalizeSession(sessionId) ?? null
    if (next === this.snapshot.session_id) return
    this.generation += 1
    this.abort?.abort()
    this.abort = undefined
    this.inFlight = false
    this.retry = 0
    this.cancelScheduled()
    this.publish({
      state: 'connecting', loading: false, data: null, error: null,
      last_success_at: null, session_id: next, generation: this.generation,
    })
    if (this.started && this.visible) this.refreshNow()
  }

  /** Manual refresh: abandon an in-flight read so the user's action is not queued behind it. */
  refreshNow(): void {
    if (!this.started || !this.visible) return
    this.cancelScheduled()
    if (this.inFlight) {
      this.generation += 1
      this.abort?.abort()
      this.abort = undefined
      this.inFlight = false
    }
    void this.refresh()
  }

  private readonly onVisibility = (): void => {
    const visible = this.visibility?.visibilityState !== 'hidden'
    if (visible === this.visible) return
    this.visible = visible
    if (!visible) {
      this.cancelScheduled()
      this.generation += 1
      this.abort?.abort()
      this.abort = undefined
      this.inFlight = false
      return
    }
    // A hidden page may have missed many intervals; re-verify immediately and
    // let a failed read enter the bounded retry ladder.
    this.refreshNow()
  }

  private async refresh(): Promise<void> {
    if (!this.started || !this.visible || this.inFlight) return
    this.inFlight = true
    const generation = ++this.generation
    // Keep the previous failure or stale success visible until this attempt
    // actually succeeds; clearing it here would flash green from old data.
    this.publish({ loading: true })
    const controller = new AbortController()
    this.abort = controller
    const timeout = this.setTimer(() => {
      controller.abort(new DOMException('Status request exceeded its deadline', 'TimeoutError'))
    }, STATUS_REQUEST_TIMEOUT_MS)
    try {
      const params = new URLSearchParams()
      if (this.snapshot.session_id !== null) params.set('session_id', this.snapshot.session_id)
      const query = params.toString()
      const response = await this.fetcher(`${this.baseUrl}${STATUS_ROUTE}${query ? `?${query}` : ''}`, {
        method: 'GET',
        headers: { accept: 'application/json' },
        cache: 'no-store',
        credentials: 'same-origin',
        redirect: 'error',
        signal: controller.signal,
      })
      if (generation !== this.generation || !this.started) return
      if (!response.ok) {
        this.fail(generation, errorFromResponse(response, await readErrorBody(response)))
        return
      }
      let parsed: unknown
      try {
        parsed = await response.json()
      } catch {
        this.fail(generation, {
          kind: 'protocol', code: 'invalid_status_response', http_status: response.status,
          message: 'The PowerContext status bridge returned a non-JSON response.',
          recovery: 'Check the installed plugin build and DSH logs.',
        })
        return
      }
      const payload = parseStatusPayload(parsed)
      if (payload === null) {
        this.fail(generation, {
          kind: 'protocol', code: 'invalid_status_response', http_status: response.status,
          message: 'The PowerContext status response did not match its documented schema.',
          recovery: 'Install matching PowerContext Server and DSH plugin versions.',
        })
        return
      }
      if (generation !== this.generation || !this.started) return
      this.retry = 0
      const now = this.now()
      this.publish({
        state: displayState({ ...this.snapshot, data: payload, error: null, last_success_at: now }, now),
        loading: false, data: payload, error: null, last_success_at: now,
        generation,
      })
      this.schedule(payload.connection.state === 'ready' ? STATUS_REFRESH_INTERVAL_MS : STATUS_CONNECTING_RETRY_MS)
    } catch (error) {
      if (generation !== this.generation || !this.started) return
      const timedOut = error instanceof Error && error.name === 'TimeoutError'
      this.fail(generation, timedOut
        ? {
            kind: 'network', code: 'status_request_timeout',
            message: 'The PowerContext status request exceeded its deadline.',
            recovery: 'Check the PowerContext Server and DSH host, then retry.',
          }
        : {
            kind: 'network', code: 'host_unreachable',
            message: 'The PowerContext status bridge could not be reached.',
            recovery: 'Check that DSH is still running, then retry.',
          })
    } finally {
      this.clearTimer(timeout)
      if (generation === this.generation) {
        this.inFlight = false
        this.abort = undefined
      }
    }
  }

  private fail(generation: number, error: StatusError): void {
    if (generation !== this.generation || !this.started) return
    this.retry += 1
    this.publish({
      state: 'failed', loading: false, error, generation,
    })
    this.schedule(STATUS_RETRY_DELAYS_MS[Math.min(this.retry - 1, STATUS_RETRY_DELAYS_MS.length - 1)]!)
  }

  private schedule(delay: number): void {
    this.cancelScheduled()
    if (!this.started || !this.visible) return
    this.timer = this.setTimer(() => {
      this.timer = undefined
      void this.refresh()
    }, delay)
  }

  private cancelScheduled(): void {
    if (this.timer !== undefined) {
      this.clearTimer(this.timer)
      this.timer = undefined
    }
  }

  private publish(patch: Partial<StatusSnapshot>): void {
    const next: StatusSnapshot = {
      ...this.snapshot,
      ...patch,
    }
    next.state = displayState(next, this.now())
    const previous = this.snapshot
    if (previous.state === next.state && previous.loading === next.loading
      && previous.data === next.data && previous.error === next.error
      && previous.last_success_at === next.last_success_at && previous.session_id === next.session_id
      && previous.generation === next.generation) return
    this.snapshot = next
    for (const listener of this.listeners) listener()
  }
}
