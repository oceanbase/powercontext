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

import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import type { Context } from '@deepseek-ai/cordis'
import {
  type DisplayState, type StatusPayload,
} from '../status-contract.ts'
import { activeSessionId, displayState, StatusController, type StatusSnapshot } from './status-controller.ts'
import { readLocale, saveLocale, texts, failureTextKey, type Locale, type TextKey } from './locale.ts'
import { STATUS_STYLES } from './styles.ts'

const LocaleContext = createContext<Locale>('zh-CN')
function useText() {
  const locale = useContext(LocaleContext)
  return (key: string): string => texts[locale][key as TextKey] ?? key
}

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
  const t = useText()
  return <>{t(state === 'failed' && snapshot.error?.kind === 'authentication' ? 'auth' : state)}</>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="pc-section" aria-label={title}><h3 className="pc-section-title">{title}</h3>{children}</section>
}

function SessionSection({ data }: { data: StatusPayload }) {
  const t = useText()
  return <Section title={t('session')}>
    {data.session === null ? <p className="pc-muted">{t('noSession')}</p> : <div className="pc-kv">
      {data.session.cwd && <><span className="pc-key">{t('workspace')}</span><span className="pc-value pc-mono">{data.session.cwd}</span></>}
      <span className="pc-key">{t('scope')}</span><span className="pc-value pc-mono">{data.session.scope_id ?? t('unresolved')}</span>
      {data.session.scope_state === 'unresolved' && <p className="pc-alert-text">{t('scopeHelp')}</p>}
    </div>}
  </Section>
}

function TechnicalDetails({ data }: { data: StatusPayload }) {
  const t = useText()
  return <details className="pc-details"><summary>{t('details')}</summary>
    <Section title={t('connection')}><div className="pc-kv">
      {data.session && <><span className="pc-key">{t('conversation')}</span><span className="pc-value pc-mono">{data.session.id}</span></>}
      {data.connection.code && <><span className="pc-key">{t('reason')}</span><span className="pc-value pc-mono">{data.connection.code} {data.connection.http_status}</span></>}
      {data.connection.message && <p className="pc-value">{data.connection.message}</p>}
      {data.connection.recovery && <p className="pc-value">{data.connection.recovery}</p>}
      {data.session?.scope_message && <p className="pc-value">{data.session.scope_code} · {data.session.scope_message}</p>}
      <span className="pc-key">{t('deployment')}</span><span className="pc-value">{data.deployment.mode === 'desktop' ? 'DSH Desktop' : 'Local Web'} · {t(data.deployment.owner_local === 'automatic' ? 'automaticOwner' : 'explicit')}</span>
      <span className="pc-key">{t('endpointSource')}</span><span className="pc-value">{data.configuration.endpoint_source}{data.configuration.path_prefix ? t('pathPrefix') : ''}</span>
      <span className="pc-key">{t('authentication')}</span><span className="pc-value">{t(data.configuration.authorization_configured ? 'configured' : 'unconfigured')} · {data.configuration.authorization_source}</span>
      {(['liveness', 'readiness', 'capabilities'] as const).map(key => <div className="pc-detail-row" key={key}>
        <span className="pc-key">{t(key)}</span><span className="pc-value">{t(data.server[key].state)} {data.server[key].code}</span>
      </div>)}
      <span className="pc-key">{t('dependencies')}</span><span className="pc-value pc-mono">{Object.entries(data.server.readiness.dependencies ?? {}).map(([key, value]) => `${key}=${value}`).join(', ') || t('unknown')}</span>
      <span className="pc-key">{t('scopeSource')}</span><span className="pc-value">{data.configuration.scope_source}</span>
      <span className="pc-key">{t('schema')}</span><span className="pc-value pc-mono">{data.capabilities.context_versions.join(', ') || t('unknown')}</span>
      <span className="pc-key">{t('memory')}</span><span className="pc-value">{t(data.capabilities.memory_extraction === null ? 'unknown' : data.capabilities.memory_extraction ? 'configured' : 'disabled')}</span>
      <span className="pc-key">{t('handoff')}</span><span className="pc-value">{t(data.capabilities.handoff_generation === null ? 'unknown' : data.capabilities.handoff_generation ? 'configured' : 'disabled')}</span>
    </div></Section>
    <Section title={t('automatic')}>
      {data.runtime.freshness === 'not_yet_observed' ? <p className="pc-muted">{t('noObservation')}</p> :
        <div className="pc-stage-list">{Object.entries(data.runtime.stages).map(([key, stage]) =>
          <div key={key} className="pc-stage" data-pc-stage={stage.operation} data-pc-stage-state={stage.state}>
            <Dot state={stage.state === 'unavailable' ? 'failed' : stage.state === 'running' ? 'connecting' : ['resolved', 'ready', 'empty', 'accepted', 'completed', 'appended'].includes(stage.state) ? 'ready' : 'unconfigured'} />
            <div><div className="pc-stage-head"><span>{t(key)}</span><span>{t(stage.state)}</span></div>
              {stage.code && <p className="pc-stage-message pc-mono">{stage.code}</p>}
              {stage.message && <p className="pc-stage-message">{stage.message}</p>}
              {stage.recovery && <p className="pc-stage-message">{stage.recovery}</p>}
              {(stage.http_status || stage.request_id || stage.protocol_issue || stage.confirmation) && <p className="pc-stage-meta pc-mono">{[stage.http_status, stage.request_id, stage.protocol_issue, stage.confirmation].filter(Boolean).join(' · ')}</p>}
              {stage.observed_at && <time className="pc-stage-meta">{stage.observed_at}</time>}
            </div>
          </div>)}</div>}
      {data.runtime.freshness === 'stale' && <p className="pc-coverage">{t('stale')} · {data.runtime.stale_reason}</p>}
      <p className="pc-coverage">{t('coverage')}</p>
    </Section>
    {data.notices.length > 0 && <p className="pc-coverage" data-pc-notices={data.notices.join(',')}>{data.notices.map(code => t(code)).join(' ')}</p>}
  </details>
}

/**
 * The sidebar footer action and its non-modal diagnostics panel. All status
 * values come from the authenticated Host bridge; the component never reads a
 * credential, calls a model, or invokes `/pc doctor`.
 */
export function PowerContextStatus(props: StatusProps) {
  const [locale, setLocale] = useState<Locale>(readLocale)
  return <LocaleContext.Provider value={locale}><StatusPanel {...props} locale={locale} changeLocale={value => { saveLocale(value); setLocale(value) }} /></LocaleContext.Provider>
}

function StatusPanel(props: StatusProps & { locale: Locale; changeLocale: (locale: Locale) => void }) {
  const t = useText()
  const [languageOpen, setLanguageOpen] = useState(false)
  const languageRef = useRef<HTMLButtonElement>(null)
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
      if (languageOpen) { setLanguageOpen(false); languageRef.current?.focus(); return }
      close()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open, languageOpen])

  const close = (): void => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'Tab') return
    const focusable = [...(panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), summary, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ) ?? [])].filter(element => element.getClientRects().length > 0)
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

  const label = t(state)
  return (
    <div className="pc-status" lang={props.locale}>
      <button
        ref={triggerRef}
        type="button"
        className={`pc-trigger${wide ? ' pc-trigger-wide' : ' pc-trigger-rail'}`}
        aria-label={`PowerContext ${t("status")}: ${label}`}
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
                <div className="pc-subtitle">{t('diagnostics')}</div>
              </div>
            </div>
            <div className="pc-actions">
              <button ref={languageRef} type="button" className="pc-icon-button" aria-label={t('language')} title={t('language')} aria-expanded={languageOpen} aria-controls={`${panelId}-language`} onClick={() => setLanguageOpen(value => !value)}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></svg>
              </button>
              <button
                type="button"
                className="pc-icon-button"
                aria-label={t('refresh')}
                title={t('refresh')}
                disabled={snapshot.loading}
                onClick={() => { setClockTick(Date.now()); refresh() }}
              >
                ↻
              </button>
              <button type="button" className="pc-icon-button" data-pc-close aria-label={t('close')} title={t('close')} onClick={close}>
                ×
              </button>
            </div>
          </header>
          {languageOpen && <fieldset id={`${panelId}-language`} className="pc-language"><legend>{t('language')}</legend>
            {(['zh-CN', 'en'] as const).map(value => <label key={value}><input type="radio" name={`${panelId}-locale`} value={value} checked={props.locale === value} onChange={() => props.changeLocale(value)} />{value === 'zh-CN' ? '简体中文' : 'English'}</label>)}
          </fieldset>}

          <div className="pc-panel-body">
            <div className={`pc-summary pc-summary-${state}`} role="status" aria-live="polite" aria-busy={snapshot.loading}>
              <Dot state={state} />
              <div className="pc-summary-copy">
                <div className="pc-summary-label"><StatusLabel state={state} snapshot={snapshot} /></div>
                <div className="pc-summary-detail">{state === 'failed' ? t(failureTextKey(snapshot.error?.code ?? data?.connection.code, snapshot.error?.http_status ?? data?.connection.http_status)) : state === 'ready' ? null : t(state + 'Help')}</div>
                {state === 'failed' && data?.connection.code && <div className="pc-stage-message pc-mono">{data.connection.code}</div>}
              </div>
              <span className="pc-summary-time">
                {snapshot.last_success_at === null ? t('notChecked') : new Date(data?.observed_at ?? snapshot.last_success_at).toLocaleTimeString(props.locale, { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>

            {snapshot.error !== null && <div className="pc-alert" role="alert">
              <p className="pc-alert-text">{t(snapshot.error.kind === 'authentication' ? 'authHelp' : snapshot.error.kind === 'forbidden' || snapshot.error.kind === 'bridge' ? 'bridgeHelp' : 'failureHelp')}</p>
              <span className="pc-mono">{snapshot.error.code}</span>
              <div className="pc-alert-actions"><button type="button" className="pc-text-button" onClick={() => { setClockTick(Date.now()); refresh() }}>{t('retry')}</button></div>
            </div>}

            {data !== null && (
              <>
                <Section title={t('server')}><div className="pc-kv"><span className="pc-key">{t('address')}</span><span className="pc-value pc-mono">{data.configuration.endpoint_origin || t('unknown')}</span></div></Section>
                <SessionSection data={data} />
                <TechnicalDetails data={data} />
              </>
            )}

            {data === null && snapshot.error === null && (
              <p className="pc-empty">{t('connectingHelp')}</p>
            )}
          </div>

          <footer className="pc-panel-foot">
            <span>{t('serverVersion')}</span>
            <span className="pc-mono">{snapshot.error !== null || state === 'stale' ? t('unknown') : data?.server.package_version ?? t('unknown')}</span>
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
