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

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import type { Context } from '@deepseek-ai/cordis'
import {
  STATUS_STALE_AFTER_MS, STATUS_REFRESH_INTERVAL_MS, type DisplayState, type StatusPayload,
  type StatusProbePayload, type StatusStagePayload,
} from '../status-contract.ts'
import { activeSessionId, displayState, StatusController, type StatusSnapshot } from './status-controller.ts'
import { STATUS_STYLES } from './styles.ts'

const STYLE_ID = 'powercontext-dsh-status-styles'

interface SessionRow {
  id?: string
  retainedBy?: { mainView?: number }
}

interface StatusFace {
  hooks: { status: StatusController }
  setSession: (sessionId: string | undefined) => void
  refresh: () => void
}

interface FooterActionProps {
  wide: boolean
  useSessions: <T>(selector: (state: { byId: Record<string, SessionRow> }) => T) => T
}

type StatusProps = FooterActionProps & StatusFace & {
  useStatus: <T>(selector: (snapshot: StatusSnapshot) => T) => T
}

interface SlotsService {
  inject(key: 'sidebar.footer.action', callback: () => () => void): void
  register(
    options: { name: 'sidebar.footer.action'; id: string; order: number; inject?: () => StatusFace },
    component: (props: StatusProps) => ReturnType<typeof PowerContextStatus>,
  ): () => void
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    slots: SlotsService
  }
}

export const inject = ['slots']

const STAGE_ROWS = [
  { key: 'scope', label: 'Scope resolution' },
  { key: 'prepare', label: 'Recall' },
  { key: 'capture', label: 'Source capture' },
  { key: 'flush', label: 'Memory flush' },
  { key: 'injection', label: 'Context injection' },
] as const

function PowerContextMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 7.5 12 3l8 4.5-8 4.5-8-4.5Z" />
      <path d="m4 12 8 4.5 8-4.5" opacity=".72" />
      <path d="m4 16.5 8 4.5 8-4.5" opacity=".45" />
    </svg>
  )
}

function Dot({ state }: { state: DisplayState }) {
  return <span className={`pc-dot pc-dot-${state}`} aria-hidden="true" />
}

function StatusLabel({ state, snapshot }: { state: DisplayState; snapshot: StatusSnapshot }) {
  if (state === 'failed' && snapshot.error?.kind === 'authentication') return <>Authentication required</>
  switch (state) {
    case 'unconfigured': return <>Not configured</>
    case 'connecting': return snapshot.error === null ? <>Connecting</> : <>Retrying</>
    case 'stale': return <>Stale, retrying</>
    case 'ready': return <>Connected</>
    case 'failed': return <>Action needed</>
  }
}

function triggerLabel(state: DisplayState, snapshot: StatusSnapshot): string {
  const label = state === 'ready' ? 'Connected'
    : state === 'connecting' ? 'Connecting'
      : state === 'stale' ? 'Status stale, retrying'
        : state === 'failed' ? 'Action needed'
          : 'Not configured'
  const error = snapshot.error
  return error === null ? label : `${label}: ${error.message}`
}

function summaryDetail(state: DisplayState, snapshot: StatusSnapshot): string {
  if (state === 'failed') return snapshot.error?.message ?? snapshot.data?.connection.message ?? 'PowerContext is not ready.'
  if (state === 'unconfigured') {
    return snapshot.error?.message ?? 'The status bridge is unavailable in this host. Existing tools still work.'
  }
  if (state === 'stale') return 'The last observation expired; re-verifying with the Server.'
  if (state === 'connecting') return 'Contacting the PowerContext Server and checking readiness.'
  if (snapshot.data?.session === null) return 'Readiness verified. There is no active conversation to observe.'
  return 'Connectivity, readiness, and the required context capability are verified.'
}

function ago(milliseconds: number | null | undefined, now: number): string {
  if (milliseconds === null || milliseconds === undefined) return 'not observed'
  const seconds = Math.max(0, Math.round(milliseconds / 1_000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${String(seconds)}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${String(minutes)}m ago`
  return `${String(Math.round(minutes / 60))}h ago`
}

function clock(iso: string | null): string {
  if (iso === null) return 'not yet'
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

function probeLabel(probe: StatusProbePayload): string {
  if (probe.state === 'ok') return 'Verified'
  if (probe.state === 'failed') return 'Failed'
  return 'Not checked'
}

function stageLabel(stage: StatusStagePayload): string {
  switch (stage.state) {
    case 'not_yet_observed': return 'Not yet observed'
    case 'running': return 'Running'
    case 'resolved': return 'Resolved'
    case 'ready': return 'Ready'
    case 'empty': return 'Empty'
    case 'accepted': return 'Accepted'
    case 'completed': return 'Completed'
    case 'incomplete': return 'Incomplete'
    case 'appended': return 'Injected'
    case 'skipped': return 'Skipped'
    case 'unavailable': return 'Unavailable'
  }
}

function stageTone(stage: StatusStagePayload): DisplayState {
  if (stage.state === 'unavailable') return 'failed'
  if (stage.state === 'running') return 'connecting'
  if (['resolved', 'ready', 'empty', 'accepted', 'completed', 'appended'].includes(stage.state)) return 'ready'
  return 'unconfigured'
}

function StageRow({ stage, label, now }: { stage: StatusStagePayload; label: string; now: number }) {
  const tone = stageTone(stage)
  return (
    <div className="pc-stage" data-pc-stage={stage.operation} data-pc-stage-state={stage.state}>
      <span className={`pc-dot pc-dot-${tone}`} aria-hidden="true" />
      <div className="pc-stage-main">
        <div className="pc-stage-head">
          <span className="pc-stage-name">{label}</span>
          <span className="pc-stage-state">{stageLabel(stage)}</span>
        </div>
        {stage.code !== undefined && <p className="pc-stage-message pc-mono">{stage.code}</p>}
        {stage.message !== undefined && <p className="pc-stage-message">{stage.message}</p>}
        <p className="pc-stage-meta">{ago(stage.age_ms ?? (stage.observed_at === null ? null : now - Date.parse(stage.observed_at)), now)}</p>
      </div>
    </div>
  )
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="pc-section" aria-label={title}>
      <div className="pc-section-head">
        <h3 className="pc-section-title">{title}</h3>
        {note !== undefined && <span className="pc-section-note">{note}</span>}
      </div>
      {children}
    </section>
  )
}

function ConnectionSection({ data }: { data: StatusPayload }) {
  const readinessDependencies = data.server.readiness.dependencies
  return (
    <Section title="Connection" note="read-only checks">
      <div className="pc-kv">
        <span className="pc-key">Deployment</span>
        <span className="pc-value">
          {data.deployment.mode === 'desktop' ? 'DSH Desktop' : 'Local Web'}
          <span className="pc-muted"> · {data.deployment.owner_local === 'explicit' ? 'explicit owner config' : 'automatic desktop signal'}</span>
        </span>
        <span className="pc-key">Endpoint</span>
        <span className="pc-value pc-mono">{data.configuration.endpoint_origin || 'unavailable'}</span>
        <span className="pc-key">Endpoint source</span>
        <span className="pc-value">{data.configuration.endpoint_source}{data.configuration.path_prefix ? ' (with path prefix)' : ''}</span>
        <span className="pc-key">Authentication</span>
        <span className="pc-value">
          {data.configuration.authorization_configured ? `Configured (${data.configuration.authorization_source})` : 'Not configured'}
          <span className="pc-muted"> · value never displayed</span>
        </span>
        <span className="pc-key">Liveness</span>
        <span className="pc-value">{probeLabel(data.server.liveness)}{data.server.liveness.code ? ` · ${data.server.liveness.code}` : ''}</span>
        <span className="pc-key">Readiness</span>
        <span className="pc-value">
          {probeLabel(data.server.readiness)}{data.server.readiness.code ? ` · ${data.server.readiness.code}` : ''}
          {readinessDependencies !== undefined && Object.keys(readinessDependencies).length > 0 && (
            <span className="pc-muted"> ({Object.entries(readinessDependencies).map(([name, value]) => `${name}=${value}`).join(', ')})</span>
          )}
        </span>
        <span className="pc-key">Required capability</span>
        <span className="pc-value">
          {data.server.capabilities.state === 'unknown'
            ? 'Not checked (liveness or readiness failed)'
            : data.capabilities.prepared_context
              ? 'PreparedContext schema verified'
              : 'PreparedContext schema missing'}
        </span>
        <span className="pc-key">Scope source</span>
        <span className="pc-value">{data.configuration.scope_source}</span>
      </div>
    </Section>
  )
}

function SessionSection({ data }: { data: StatusPayload }) {
  if (data.session === null) {
    return (
      <Section title="Scope and conversation">
        <p className="pc-empty">
          No active conversation. This panel reads status only; it does not create a conversation or change Scope.
        </p>
      </Section>
    )
  }
  return (
    <Section title="Scope and conversation">
      <div className="pc-kv">
        <span className="pc-key">Conversation</span>
        <span className="pc-value pc-mono">{data.session.id}</span>
        {data.session.cwd !== undefined && (
          <>
            <span className="pc-key">Workspace</span>
            <span className="pc-value pc-mono">{data.session.cwd}</span>
          </>
        )}
        <span className="pc-key">Active Scope</span>
        <span className="pc-value pc-mono">
          {data.session.scope_state === 'resolved' && data.session.scope_id !== null
            ? data.session.scope_id
            : 'unresolved'}
        </span>
        {data.session.scope_state === 'unresolved' && data.session.scope_message !== undefined && (
          <>
            <span className="pc-key">Scope detail</span>
            <span className="pc-value">
              {data.session.scope_message}
              {data.session.scope_code !== undefined && <span className="pc-muted"> · {data.session.scope_code}</span>}
            </span>
          </>
        )}
      </div>
    </Section>
  )
}

function RuntimeSection({ data, now }: { data: StatusPayload; now: number }) {
  const runtime = data.runtime
  return (
    <Section title="Automatic path" note={runtime.freshness === 'stale' ? 'stale observation' : undefined}>
      {runtime.freshness === 'not_yet_observed' ? (
        <p className="pc-empty">
          No automatic-path attempt has been observed for this conversation. Send a prompt in DSH, then refresh.
        </p>
      ) : (
        <div className="pc-stage-list">
          {STAGE_ROWS.map(row => {
            const stage = runtime.stages[row.key]
            return stage === undefined ? null : <StageRow key={row.key} stage={stage} label={row.label} now={now} />
          })}
        </div>
      )}
      {runtime.freshness === 'stale' && runtime.stale_reason !== undefined && (
        <p className="pc-coverage">Stale reason: {runtime.stale_reason}. The sidebar will not present this as a fresh connection.</p>
      )}
      {runtime.coverage !== '' && <p className="pc-coverage">{runtime.coverage}</p>}
    </Section>
  )
}

function CapabilitySection({ data }: { data: StatusPayload }) {
  const memory = data.capabilities.memory_extraction
  const handoff = data.capabilities.handoff_generation
  return (
    <Section title="Server capabilities">
      <div className="pc-kv">
        <span className="pc-key">Memory extraction</span>
        <span className="pc-value">{memory === null ? 'Unknown' : memory ? 'Configured' : 'Disabled'}</span>
        <span className="pc-key">Handoff generation</span>
        <span className="pc-value">{handoff === null ? 'Unknown' : handoff ? 'Configured' : 'Disabled'}</span>
        <span className="pc-key">Context versions</span>
        <span className="pc-value pc-mono">
          {data.capabilities.context_versions.length === 0 ? 'unknown' : data.capabilities.context_versions.join(', ')}
        </span>
      </div>
    </Section>
  )
}

function Notices({ notices }: { notices: string[] }) {
  const messages = notices.map(notice => {
    switch (notice) {
      case 'no_active_session': return 'No active conversation; none was created.'
      case 'session_unavailable': return 'The selected conversation is no longer available to this host.'
      case 'session_service_unavailable': return 'DSH did not expose its session registry to the status bridge.'
      case 'scope_unresolved': return 'The current Scope could not be resolved; Scope-dependent work may be unavailable.'
      default: return notice
    }
  })
  if (messages.length === 0) return null
  return (
    <div className="pc-alert" role="status" data-pc-notices={notices.join(',')}>
      <div className="pc-alert-title">Attention</div>
      <p className="pc-alert-text">{messages.join(' ')}</p>
    </div>
  )
}

/**
 * The sidebar footer action and its non-modal diagnostics panel. All status
 * values come from the authenticated Host bridge; the component never reads a
 * credential, calls a model, or invokes `/pc doctor`.
 */
export function PowerContextStatus(props: StatusProps) {
  const { wide, useSessions, useStatus, setSession, refresh } = props
  const sessionId = useSessions(state => activeSessionId(state))
  const snapshot = useStatus(value => value)
  const [open, setOpen] = useState(false)
  const [clockTick, setClockTick] = useState(() => Date.now())
  const [anchor, setAnchor] = useState({ left: 12, bottom: 12 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const panelId = useId()
  const now = Math.max(clockTick, snapshot.last_success_at ?? 0)
  const state = displayState(snapshot, now)
  const data = snapshot.data

  useEffect(() => { setSession(sessionId) }, [sessionId, setSession])

  useEffect(() => {
    if (!open) return
    const timer = window.setInterval(() => { setClockTick(Date.now()) }, 10_000)
    return () => { window.clearInterval(timer) }
  }, [open])

  useEffect(() => {
    if (!open) return
    // Native keyboard activation can return focus to the trigger after the
    // click handler; settle the dialog focus one task later as well.
    const timer = window.setTimeout(() => {
      const panel = panelRef.current
      if (panel === null || !panel.contains(document.activeElement)) {
        panel?.focus({ preventScroll: true })
      }
    }, 50)
    return () => { window.clearTimeout(timer) }
  }, [open])

  useLayoutEffect(() => {
    if (!open) return
    const place = (): void => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (rect === undefined) return
      const width = Math.min(392, window.innerWidth - 24)
      setAnchor({
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        bottom: Math.max(12, window.innerHeight - rect.top + 8),
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    const frame = window.requestAnimationFrame(() => {
      const panel = panelRef.current
      if (panel === null) return
      // Focus the non-modal dialog itself first; focusing an element while its
      // sidebar ancestor is still becoming visible would otherwise be dropped.
      panel.focus({ preventScroll: true })
      panel.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus({ preventScroll: true })
    })
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const close = (): void => { setOpen(false); triggerRef.current?.focus() }
    const onPointerDown = (event: PointerEvent): void => {
      const target = event.target
      if (target instanceof Node && panelRef.current?.contains(target)) return
      if (target instanceof Node && triggerRef.current?.contains(target)) return
      close()
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      close()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open])

  const close = (): void => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'Tab') return
    const focusable = [...(panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ) ?? [])]
    if (focusable.length === 0) return
    const first = focusable[0]!
    const last = focusable[focusable.length - 1]!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const label = triggerLabel(state, snapshot)
  return (
    <div className="pc-status">
      <button
        ref={triggerRef}
        type="button"
        className={`pc-trigger${wide ? ' pc-trigger-wide' : ' pc-trigger-rail'}`}
        aria-label={`PowerContext status: ${label}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={label}
        data-pc-status-trigger={state}
        onClick={() => { setClockTick(Date.now()); setOpen(value => !value) }}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' && event.key !== ' ') return
          // Native button activation returns focus to the trigger after the
          // click; open explicitly so the dialog focus effect is deterministic.
          event.preventDefault()
          setClockTick(Date.now())
          setOpen(true)
        }}
      >
        <span className="pc-mark"><PowerContextMark size={wide ? 20 : 18} /><Dot state={state} /></span>
        <span className="pc-trigger-label">PowerContext</span>
        {wide && <span className="pc-trigger-state"><StatusLabel state={state} snapshot={snapshot} /></span>}
      </button>

      {open && (
        <div
          ref={panelRef}
          id={panelId}
          className="pc-panel"
          role="dialog"
          aria-modal="false"
          aria-labelledby={`${panelId}-title`}
          tabIndex={-1}
          style={{ left: anchor.left, bottom: anchor.bottom }}
          onKeyDown={onPanelKeyDown}
          data-pc-status-panel={state}
        >
          <header className="pc-panel-head">
            <div className="pc-brand">
              <span className="pc-mark"><PowerContextMark size={22} /></span>
              <div>
                <h2 className="pc-title" id={`${panelId}-title`}>PowerContext</h2>
                <div className="pc-subtitle">Connection diagnostics</div>
              </div>
            </div>
            <div className="pc-actions">
              <button
                type="button"
                className="pc-icon-button"
                aria-label="Refresh status"
                title="Refresh status"
                disabled={snapshot.loading}
                onClick={() => { setClockTick(Date.now()); refresh() }}
              >
                ↻
              </button>
              <button type="button" className="pc-icon-button" aria-label="Close diagnostics" title="Close" onClick={close}>
                ×
              </button>
            </div>
          </header>

          <div className="pc-panel-body">
            <div className={`pc-summary pc-summary-${state}`} role="status" aria-live="polite" aria-busy={snapshot.loading}>
              <Dot state={state} />
              <div className="pc-summary-copy">
                <div className="pc-summary-label"><StatusLabel state={state} snapshot={snapshot} /></div>
                <div className="pc-summary-detail">{summaryDetail(state, snapshot)}</div>
              </div>
              <span className="pc-summary-time">
                {snapshot.last_success_at === null ? 'not checked' : clock(data?.observed_at ?? null)}
              </span>
            </div>

            {snapshot.error !== null && (state === 'failed' ? (
              <div className="pc-alert" role="alert">
                <div className="pc-alert-title">Status read failed</div>
                <p className="pc-alert-text"><span className="pc-mono">{snapshot.error.code}</span> · {snapshot.error.message}</p>
                {snapshot.error.recovery !== undefined && <p className="pc-alert-text">{snapshot.error.recovery}</p>}
                <div className="pc-alert-actions">
                  <button type="button" className="pc-text-button" onClick={() => { setClockTick(Date.now()); refresh() }}>Retry</button>
                </div>
              </div>
            ) : (
              <div className="pc-empty" role="status" data-pc-status-unconfigured={snapshot.error.code}>
                {snapshot.error.recovery}
                <div className="pc-alert-actions">
                  <button type="button" className="pc-text-button" onClick={() => { setClockTick(Date.now()); refresh() }}>Retry</button>
                </div>
              </div>
            ))}

            {data !== null && (
              <>
                <Notices notices={data.notices} />
                <ConnectionSection data={data} />
                <SessionSection data={data} />
                <RuntimeSection data={data} now={now} />
                <CapabilitySection data={data} />
              </>
            )}

            {data === null && snapshot.error === null && (
              <p className="pc-empty">Reading the local status bridge. No model request is made.</p>
            )}
          </div>

          <footer className="pc-panel-foot">
            <span>Local DSH owner only · read-only checks</span>
            <span className="pc-mono">{data?.plugin_version ?? 'checking'}</span>
          </footer>
        </div>
      )}
    </div>
  )
}

function installStyles(ctx: Context): void {
  if (typeof document === 'undefined') return
  ctx.effect(() => {
    document.getElementById(STYLE_ID)?.remove()
    const style = document.createElement('style')
    style.id = STYLE_ID
    style.textContent = STATUS_STYLES
    document.head.append(style)
    return () => { style.remove() }
  }, 'powercontext: sidebar status styles')
}

/**
 * Register the optional sidebar entry. The sidebar slot owner controls when the
 * component mounts; unloading this plugin disposes the slot cell, styles, and
 * the controller's timers.
 */
export function apply(ctx: Context): void {
  installStyles(ctx)
  const controller = new StatusController()
  ctx.effect(() => () => controller.dispose(), 'powercontext: status controller disposal')
  ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'powercontext-status',
    order: 40,
    inject: (): StatusFace => ({
      hooks: { status: controller },
      setSession: sessionId => controller.setSession(sessionId),
      refresh: () => controller.refreshNow(),
    }),
  }, PowerContextStatus))
}
