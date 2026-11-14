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

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { IconCheckCircleOutlineRegular, IconChevronLeftOutlineRegular, IconClockOutlineRegular, IconShareOutlineRegular, Menu, MenuItemButton, Tooltip } from '@deepseek-ai/dsh-client-ui-primitives'
import type { DeliveryView, SourceBoundary } from '../../handoff/contracts.ts'
import type { PreflightView } from '../../handoff/preflight.ts'
import type { HandoffLocale } from '../../handoff/locale.ts'
import type { LaunchView } from '../../handoff/launch.ts'
import { readLocale, subscribeLocale } from '../locale.ts'
import { PowerContextMark } from '../brand.tsx'
import { HandoffHttp, HandoffHttpError, workInput, type DeliverySnapshot, type HandoffPorts, type Target, type TargetDiagnostic } from './client.ts'
import type { ReceiptView, ReceiptReadState } from '../../handoff/receipt-contract.ts'

type Chat = { timeline: { turnOrder: readonly number[] }; locations: { getTurn(turn: number): readonly string[] };
  nodes: { get(key: string): { kind: string; data: { closing?: { finalNode?: { messageId?: string } } | null } } | undefined } }
export interface HandoffActionProps {
  sessionId: string
  messageId: string
  useChat<T>(selector: (snapshot: Chat) => T): T
  ports?: HandoffPorts
}

const copy = {
  zh: { action: '交接给其他 Agent', title: '交接当前工作', close: '关闭', unavailable: '交接边界服务不可用。请等待集成完成。',
    target: '目标', select: '选择目标', check: '重新检查目标', back: '返回', diagnostics: '安装检查', checkingInstallation: '正在检查', checkFailed: '检查失败', checkUnavailable: '检查不可用', loading: '正在加载目标', notAvailable: '目标当前不可用', unqualifiedShort: '当前版本尚未通过交接验证', installationPresent: '已安装，版本', installationMissing: '未检测到安装', installationUnknown: '无法确定安装状态', installationUnsupported: '当前平台不支持检查', unqualified: '当前版本尚未通过交接验证。可以检查安装状态；交接准备和启动暂不可用。', preflight: '预检与安装', preparation: '准备', launch: '启动', receipt: '接收确认',
    original: '继续使用原来的回复', yes: '同意安装', no: '不安装并取消', verify: '已完成，重新检查', prepare: '整理交接', manual: '手动填写并检查交接内容',
    disposition: '工作状态', selectDisposition: '选择状态', continuable: '可以继续', blocked: '受阻', complete: '已完成', objective: '工作目标', state: '当前状态与已知问题', next: '下一步', omissions: '遗漏或未验证项（每行一项）',
    newVersion: '明确创建新的交接', submit: '使用填写内容准备', open: '打开目标', retry: '明确重试打开', cancel: '取消交接', refresh: '刷新状态',
    pinned: '已固定', pin: '固定', unpin: '取消固定', delete: '删除', confirmDelete: '确认删除', keep: '保留', noHistory: '此会话没有已保存的交接', export: '导出交接包', history: '交接记录', deliveryId: '交付 ID', recover: '查看交付', timeout: '五分钟内尚未确认，可手动刷新。',
    receiptState: '接收状态', receiptRead: '本次读取', noReceipt: '尚未读取', selfReported: '接收方自行报告', authenticated: '已认证的接收方', receiver: '接收方', principal: '认证身份', receiptUnavailable: '回执读取服务不可用', instruction: '打开仅表示系统接受请求。请在目标中检查并发送预填提示。',
    interrupted: '已请求停止，结果可能未知。请刷新预检或在交接历史中恢复；已完成的安装不会自动撤销。', processing: '正在处理…', fixedOmission: '交接会注明无法取得历史工作区快照。', reset: '重新选择目标', error: '操作未完成', boundary: '回复已变化，请重新选择最新回复。', empty: '请选择支持预检的目标。' },
  en: { action: 'Hand off to another agent', title: 'Hand off current work', close: 'Close', unavailable: 'The handoff boundary service is unavailable. Integration is required.',
    target: 'Target', select: 'Choose a target', check: 'Check target again', back: 'Back', diagnostics: 'Installation checks', checkingInstallation: 'Checking', checkFailed: 'Check failed', checkUnavailable: 'Check unavailable', loading: 'Loading targets', notAvailable: 'Target unavailable', unqualifiedShort: 'Current version not yet validated for handoff', installationPresent: 'Installed, version', installationMissing: 'Installation not detected', installationUnknown: 'Installation status unknown', installationUnsupported: 'Installation check is not supported on this platform', unqualified: 'This version has not yet been validated for handoff. You can check installation; preparation and launch are unavailable.', preflight: 'Preflight and installation', preparation: 'Preparation', launch: 'Launch', receipt: 'Receipt',
    original: 'Continue with the original reply', yes: 'Allow installation', no: 'Decline and cancel', verify: 'Done, check again', prepare: 'Prepare handoff', manual: 'Enter and review handoff details',
    disposition: 'Work disposition', selectDisposition: 'Choose a status', continuable: 'Continuable', blocked: 'Blocked', complete: 'Complete', objective: 'Objective', state: 'Current state and known issues', next: 'Next action', omissions: 'Omissions or unverified items (one per line)',
    newVersion: 'Explicitly create a new handoff', submit: 'Prepare with these details', open: 'Open target', retry: 'Retry opening explicitly', cancel: 'Cancel handoff', refresh: 'Refresh status',
    pinned: 'Pinned', pin: 'Pin', unpin: 'Unpin', delete: 'Delete', confirmDelete: 'Confirm deletion', keep: 'Keep', noHistory: 'No saved handoffs in this session', export: 'Export handoff package', history: 'Handoff history', deliveryId: 'Delivery ID', recover: 'View delivery', timeout: 'No confirmation after five minutes. Refresh manually.',
    receiptState: 'Receipt status', receiptRead: 'Latest read', noReceipt: 'Not read yet', selfReported: 'Receiver self-report', authenticated: 'Authenticated receiver', receiver: 'Receiver', principal: 'Principal', receiptUnavailable: 'Receipt reader unavailable', instruction: 'Opened means the system accepted the request. Review and send the prefilled prompt in the target.',
    interrupted: 'Stop requested; the outcome may be unknown. Refresh preflight or recover from history. Completed installation is not undone.', processing: 'Working…', fixedOmission: 'The handoff will note that the historical workspace snapshot was unavailable.', reset: 'Choose a target again', error: 'The action did not complete', boundary: 'The reply changed. Select the latest reply again.', empty: 'Choose a target that supports preflight.' },
} as const

const statusZh: Record<string, string> = {
  checking_agent: '正在检查目标', checking_integration: '正在检查集成', needs_install_consent: '等待安装确认',
  installing: '正在安装', verifying: '正在验证', ready: '已就绪', needs_user_action: '需要操作',
  failed: '失败', cancelled: '已取消', unknown: '结果未知', preparing: '正在准备', needs_input: '需要填写内容',
  prepared: '已准备', requested: '已请求', opened: '已打开', awaiting: '等待接收确认',
  stale: '历史确认，未重新查询', not_found: '未找到回执', verified: '已验证', unavailable: '服务不可用', forbidden: '无读取权限', invalid: '回执无效',
  accepted: '接收方已确认', needs_clarification: '接收方需要澄清', declined: '接收方已拒绝',
  install_agent: '请安装目标应用', install_integration: '请安装集成', repair_integration: '请修复集成',
  restart_target: '请重启目标应用', sign_in: '请登录目标应用', configure_target: '请配置目标应用',
  reselect_source: '请重新选择来源', run_preflight_again: '请重新检查', check_target: '请检查目标状态',
}
function status(value: string | undefined, locale: 'zh' | 'en'): string {
  if (!value) return ''
  return locale === 'zh' ? statusZh[value] ?? value : value === 'stale' ? 'Historical confirmation, not rechecked' : value.replaceAll('_', ' ')
}
const flowCopy = {
  zh: {
    checking: '检查目标环境', installing: '安装 PowerContext', preparing: '整理当前工作', opening: '打开新会话',
    waiting: '可在目标 Agent 补充要求，再发送预填草稿。', received: '接收方已读取并确认本次交接。',
    uncertain: '结果还未确认。先检查目标会话或在交接记录中查看，再决定是否重试。',
    source: '回复已变化，请选择最新回复或明确继续原回复。',
    missing: '还没有足够的可用上下文。可以换一条有任务背景的回复，或补充必要信息。',
    unavailable: '无法继续交接。请检查目标环境后再试。',
    details: '交付详情', identifier: '交付编号', refreshDelivery: '刷新交付状态', refreshReceipt: '刷新接收确认', optional: '补充下一步和未验证项',
  },
  en: {
    checking: 'Checking target', installing: 'Installing PowerContext', preparing: 'Preparing handoff', opening: 'Opening a new session',
    waiting: 'Send the prefilled draft in the target agent.', received: 'The receiver read and acknowledged this handoff.',
    uncertain: 'The outcome is unknown. Check the target session or handoff history before trying again.',
    source: 'The reply changed. Select the latest reply or confirm the original.',
    missing: 'There is not enough usable context. Choose a reply with task context or add the missing details.',
    unavailable: 'Handoff could not continue. Check the target environment and try again.',
    details: 'Delivery details', identifier: 'Delivery ID', refreshDelivery: 'Refresh delivery', refreshReceipt: 'Refresh receipt', optional: 'Add next steps and gaps',
  },
} as const
function historyTime(value: string, locale: 'zh' | 'en'): string {
  const time = new Date(value)
  return Number.isFinite(time.getTime()) ? new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(time) : value
}

// In-memory request identities survive slot remounts without writing session content to disk.
// Durable deliveries themselves remain owned by the Host history API.
interface PendingOperations {
  users: number
  preflights: Map<string, { requestId: string; source: SourceBoundary }>
  preparations: Map<string, { requestId: string; preflight: PreflightView; locale: HandoffLocale; input?: ReturnType<typeof workInput> }>
  launches: Map<string, { requestId: string; locale: HandoffLocale; baseline: string | null }>
}
const pendingByReply = new Map<string, PendingOperations>()
const nativeTargets = [
  { id: 'codex-desktop', name: 'Codex Desktop' },
  { id: 'claude-desktop', name: 'Claude Desktop (Code)' },
  { id: 'claude-code-cli', name: 'Claude Code CLI' },
] as const
type DiagnosticStatus = TargetDiagnostic | { state: 'checking' | 'error' }
function pendingOperations(sessionId: string, messageId: string): PendingOperations {
  const key = JSON.stringify([sessionId, messageId])
  let pending = pendingByReply.get(key)
  if (!pending) {
    pending = { users: 0, preflights: new Map(), preparations: new Map(), launches: new Map() }
    pendingByReply.set(key, pending)
  }
  return pending
}
function releasePending(sessionId: string, messageId: string, pending: PendingOperations): void {
  const key = JSON.stringify([sessionId, messageId])
  if (pending.users === 0 && pending.preflights.size === 0 && pending.preparations.size === 0 && pending.launches.size === 0
    && pendingByReply.get(key) === pending) pendingByReply.delete(key)
}

export function HandoffAction(props: HandoffActionProps) {
  return <HandoffActionInstance key={JSON.stringify([props.sessionId, props.messageId])} {...props} />
}

function HandoffActionInstance(props: HandoffActionProps) {
  const latest = props.useChat(snapshot => {
    const turn = snapshot.timeline.turnOrder.at(-1)
    if (turn === undefined) return false
    return snapshot.locations.getTurn(turn).some(key => {
      const node = snapshot.nodes.get(key)
      return node?.kind === 'turn-tail' && node.data.closing?.finalNode?.messageId === props.messageId
    })
  })
  const [open, setOpen] = useState(false)
  const [visited, setVisited] = useState(false)
  const locale = useSyncExternalStore(subscribeLocale, readLocale) === 'en' ? 'en' : 'zh'
  if (!latest && !visited) return null
  return <HandoffPanel {...props} locale={locale} active={open} visited={visited} latest={latest}
    onOpen={() => { setVisited(true); setOpen(true) }} onClose={() => setOpen(false)} />
}

function HandoffPanel({ sessionId, messageId, ports, locale, active, visited, latest, onOpen, onClose }: HandoffActionProps & { locale: 'zh' | 'en'; active: boolean; visited: boolean; latest: boolean; onOpen(): void; onClose(): void }) {
  const t = copy[locale]
  const f = flowCopy[locale]
  const api = useRef(new HandoffHttp()).current
  const pending = useRef(pendingOperations(sessionId, messageId)).current
  useEffect(() => {
    pending.users++
    return () => { pending.users--; releasePending(sessionId, messageId, pending) }
  }, [pending, sessionId, messageId])
  const [replySource, setReplySource] = useState<SourceBoundary>()
  const [confirmedOriginal, setConfirmedOriginal] = useState(false)
  const [recoveredFromHistory, setRecoveredFromHistory] = useState(false)
  const [targets, setTargets] = useState<Target[]>([])
  const [view, setView] = useState<'targets' | 'flow' | 'diagnostics' | 'history'>('targets')
  const [target, setTarget] = useState('')
  const [diagnostics, setDiagnostics] = useState<Record<string, DiagnosticStatus>>({})
  const [preflight, setPreflight] = useState<PreflightView>()
  const [delivery, setDelivery] = useState<DeliverySnapshot>()
  const source = delivery?.delivery.source ?? replySource
  const [attempt, setAttempt] = useState<LaunchView>()
  const [receipt, setReceipt] = useState<ReceiptView>()
  const [receiptRead, setReceiptRead] = useState<ReceiptReadState>()
  const [timedOut, setTimedOut] = useState(false)
  const [manual, setManual] = useState(false)
  const [disposition, setDisposition] = useState<'' | 'continuable' | 'blocked' | 'complete'>('')
  const [objective, setObjective] = useState('')
  const [state, setState] = useState('')
  const [next, setNext] = useState('')
  const [omissions, setOmissions] = useState('')
  const [history, setHistory] = useState<DeliveryView[]>()
  const [deleteId, setDeleteId] = useState<string>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [phase, setPhase] = useState<'checking' | 'installing' | 'preparing' | 'opening'>('checking')
  const latestReply = useRef(latest)
  latestReply.current = latest
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const lock = useRef(false)
  const activeRequest = useRef<AbortController>()
  const activeDeliveryId = useRef<string>()
  const pendingPreparations = useRef(pending.preparations)
  const pendingLaunches = useRef(pending.launches)
  const trigger = useRef<HTMLButtonElement>(null)
  const focusPanel = useCallback((node: HTMLDivElement | null) => {
    node?.focus({ preventScroll: true })
  }, [])
  const run = async (fn: (signal: AbortSignal) => Promise<void>) => {
    if (lock.current) return
    const controller = new AbortController()
    activeRequest.current = controller; lock.current = true; setBusy(true); setError('')
    try { await fn(controller.signal) } catch (e) {
      setError(controller.signal.aborted ? 'outcome_unknown' : e instanceof Error ? e.message : 'integration_unavailable')
    } finally { activeRequest.current = undefined; lock.current = false; setBusy(false); releasePending(sessionId, messageId, pending) }
  }
  const stop = () => {
    if (activeRequest.current) activeRequest.current.abort()
    else if (delivery) void run(async signal => acceptDelivery(await api.cancel(delivery.delivery.deliveryId, sessionId, signal), true))
    else if (preflight) void run(async signal => setPreflight(await api.cancelPreflight(preflight, signal)))
  }
  useEffect(() => {
    let active = true
    if (!ports || !visited) return
    void Promise.all([ports.resolveBoundary(sessionId, messageId), api.targets()]).then(([boundary, result]) => {
      if (!active) return
      if (boundary.sessionId !== sessionId || boundary.messageId !== messageId) { setError('binding_mismatch'); return }
      setReplySource(Object.freeze({ ...boundary }))
      setTargets(result.targets)
    }).catch(() => { if (active) setError('integration_unavailable') })
    return () => { active = false }
  }, [api, ports, sessionId, messageId, visited])
  useEffect(() => {
    if (!active || view !== 'diagnostics') return
    const controller = new AbortController()
    const available = nativeTargets.filter(item => targets.some(target => target.targetId === item.id && target.diagnostics === 'available'))
    setDiagnostics(Object.fromEntries(available.map(item => [item.id, { state: 'checking' as const }])))
    for (const item of available) {
      void api.diagnoseTarget(item.id, controller.signal).then(result => {
        if (!controller.signal.aborted) setDiagnostics(current => ({ ...current, [item.id]: result }))
      }, () => {
        if (!controller.signal.aborted) setDiagnostics(current => ({ ...current, [item.id]: { state: 'error' } }))
      })
    }
    return () => { controller.abort() }
  }, [active, view, api, targets])
  const readReceipt = async (id: string, isCurrent: () => boolean = () => true): Promise<ReceiptView | undefined> => {
    if (!ports?.readReceipt) { if (isCurrent()) setReceiptRead('unavailable'); return undefined }
    try {
      const value = await ports.readReceipt(id, sessionId)
      if (!isCurrent() || activeDeliveryId.current !== id) return undefined
      setReceipt(value); setReceiptRead(value.readState)
      if (value.state !== 'awaiting') setTimedOut(false)
      return value
    } catch (error) {
      if (!isCurrent() || activeDeliveryId.current !== id) return undefined
      setReceiptRead(error instanceof HandoffHttpError && error.status === 403 ? 'forbidden'
        : error instanceof HandoffHttpError && error.message === 'receipt_invalid' ? 'invalid' : 'unavailable')
      return undefined
    }
  }
  useEffect(() => {
    if (!active || view !== 'flow' || !delivery || !attempt || !['opened', 'unknown'].includes(attempt.state) || !ports?.readReceipt) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const start = Date.parse(attempt.createdAt)
    let delay = 2000
    let inFlight = false
    const tick = async () => {
      if (stopped || inFlight || document.visibilityState === 'hidden') return
      if (!Number.isFinite(start) || Date.now() - start >= 300000) { setTimedOut(true); return }
      inFlight = true
      try {
        const result = await readReceipt(delivery.delivery.deliveryId, () => !stopped)
        if (stopped) return
        if (result && ['accepted', 'needs_clarification', 'declined'].includes(result.state)) return
      } catch { if (!stopped) setReceiptRead('unavailable') }
      finally { inFlight = false }
      if (!stopped && !document.hidden) {
        timer = setTimeout(tick, Math.min(delay, Math.max(0, 300000 - (Date.now() - start))))
        delay = Math.min(delay * 2, 30000)
      }
    }
    const visibility = () => { if (timer) clearTimeout(timer); if (document.visibilityState !== 'hidden') void tick() }
    document.addEventListener('visibilitychange', visibility); void tick()
    return () => { stopped = true; if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', visibility) }
  }, [active, view, delivery?.delivery.deliveryId, attempt?.launchAttemptId, ports, sessionId])
  const id = () => crypto.randomUUID()
  const acceptDelivery = (value: DeliverySnapshot, fromHistory = false, expected = source) => {
    const actual = value.delivery.source
    const bound = value.preflight?.source
    if (bound && (bound.sessionId !== actual.sessionId || bound.messageId !== actual.messageId
      || bound.throughSeq !== actual.throughSeq || bound.cwd !== actual.cwd || bound.workspaceObservedAt !== actual.workspaceObservedAt
      || value.preflight?.preflightId !== value.delivery.binding.preflightId
      || value.preflight?.targetId !== value.delivery.binding.targetId)) throw new Error('binding_mismatch')
    if (actual.sessionId !== sessionId || (!fromHistory && (!expected || actual.messageId !== expected.messageId
      || actual.throughSeq !== expected.throughSeq || actual.cwd !== expected.cwd || actual.workspaceObservedAt !== expected.workspaceObservedAt))) throw new Error('binding_mismatch')
    const lastAttempt = value.launchAttempts.at(-1)
    const pendingLaunch = pendingLaunches.current.get(value.delivery.deliveryId)
    if (pendingLaunch && lastAttempt && lastAttempt.launchAttemptId !== pendingLaunch.baseline) {
      // The exact new attempt is now persisted; a later explicit retry must use a new key and retryOf.
      pendingLaunches.current.delete(value.delivery.deliveryId)
    }
    activeDeliveryId.current = value.delivery.deliveryId
    if (fromHistory && delivery?.delivery.deliveryId !== value.delivery.deliveryId) { setRecoveredFromHistory(true); setConfirmedOriginal(false) }
    if (delivery?.delivery.deliveryId !== value.delivery.deliveryId) {
      setReceipt(undefined); setReceiptRead(undefined); setTimedOut(false)
      const draftKey = value.preflight && preparationKey(value.preflight.preflightId, value.delivery.deliveryId, 'manual')
      setManual(!!draftKey && pendingPreparations.current.has(draftKey))
      setDisposition(''); setObjective(''); setState(''); setNext(''); setOmissions('')
    }
    setDelivery(value); setPreflight(value.preflight ?? undefined); setAttempt(value.launchAttempts.at(-1))
  }
  const preparationKey = (preflightId: string, predecessor: string | null, kind: 'automatic' | 'manual') =>
    JSON.stringify([preflightId, predecessor, kind])
  const manualKey = preflight && delivery ? preparationKey(preflight.preflightId, delivery.delivery.deliveryId, 'manual') : undefined
  const pendingManual = manualKey ? pendingPreparations.current.get(manualKey) : undefined
  const pendingInput = pendingManual?.input
  const requestLocale = () => readLocale()
  const canHandoff = (targetId: string) => targets.some(item => item.targetId === targetId && item.availability === 'requires_preflight')
  const boundTarget = targets.find(item => item.targetId === (delivery?.delivery.binding.targetId ?? preflight?.targetId))
  const launchDelivery = async (value: DeliverySnapshot, signal: AbortSignal) => {
    signal.throwIfAborted()
    const item = value.delivery
    const baseline = value.launchAttempts.at(-1)?.launchAttemptId ?? null
    const pending = pendingLaunches.current.get(item.deliveryId) ?? { requestId: id(), baseline, locale: requestLocale() }
    pendingLaunches.current.set(item.deliveryId, pending)
    setPhase('opening')
    const result = await api.launch(item.deliveryId, sessionId, item.binding.targetId, pending.requestId, pending.baseline ?? undefined, signal, pending.locale)
    pendingLaunches.current.delete(item.deliveryId)
    setAttempt(result); setTimedOut(false)
  }
  const prepareDelivery = async (verified: PreflightView, signal: AbortSignal, autoOpen: boolean, allowOriginal = false, expected = source) => {
    if (!mounted.current || (!latestReply.current && !allowOriginal) || verified.state !== 'ready') return
    signal.throwIfAborted()
    setPhase('preparing')
    const key = preparationKey(verified.preflightId, null, 'automatic')
    const interrupted = pendingPreparations.current.get(key)
    const pending = interrupted ?? { requestId: id(), preflight: verified, locale: requestLocale() }
    pendingPreparations.current.set(key, pending)
    const result = await api.prepare(pending.preflight, pending.requestId, undefined, undefined, signal, pending.locale)
    if (result.delivery.binding.preflightId !== verified.preflightId || result.delivery.binding.targetId !== verified.targetId) throw new Error('binding_mismatch')
    acceptDelivery(result, false, expected)
    pendingPreparations.current.delete(key)
    // A fresh selection authorizes one launch; recovery and an interrupted request
    // must never dispatch again from a render, refresh or remount.
    if (autoOpen && !interrupted && mounted.current && (latestReply.current || allowOriginal) && !signal.aborted
      && result.delivery.preparationState === 'prepared' && !result.delivery.expired && result.launchAttempts.length === 0
      && !pendingLaunches.current.has(result.delivery.deliveryId)) await launchDelivery(result, signal)
  }
  const clearFlow = () => {
    activeDeliveryId.current = undefined
    setTarget(''); setPreflight(undefined); setDelivery(undefined); setAttempt(undefined)
    setReceipt(undefined); setReceiptRead(undefined); setTimedOut(false)
    setConfirmedOriginal(false); setRecoveredFromHistory(false); setError('')
    setManual(false); setDisposition(''); setObjective(''); setState(''); setNext(''); setOmissions('')
  }
  const startPreflight = (targetId: string) => {
    if (!replySource || !latest || !canHandoff(targetId) || lock.current) return
    const shownTarget = delivery?.delivery.binding.targetId ?? preflight?.targetId
    const belongsToReply = source?.messageId === messageId && source.throughSeq === replySource.throughSeq
      && source.cwd === replySource.cwd && source.workspaceObservedAt === replySource.workspaceObservedAt
    setView('flow')
    if (shownTarget === targetId && belongsToReply) return
    clearFlow()
    setTarget(targetId)
    void run(async signal => {
      setPhase('checking')
      const saved = pending.preflights.get(targetId)
      const requested = saved ?? { requestId: id(), source: replySource }
      pending.preflights.set(targetId, requested)
      const checked = await api.preflight(requested.source, targetId, requested.requestId, signal)
      if (checked.targetId !== targetId || checked.source.sessionId !== sessionId || checked.source.messageId !== messageId
        || checked.source.throughSeq !== replySource.throughSeq || checked.source.cwd !== replySource.cwd
        || checked.source.workspaceObservedAt !== replySource.workspaceObservedAt) throw new Error('binding_mismatch')
      setPreflight(checked)
      pending.preflights.delete(targetId)
      if (!saved) await prepareDelivery(checked, signal, true, false, replySource)
    })
  }
  const returnToTargets = () => {
    if (view === 'flow') activeRequest.current?.abort()
    setDeleteId(undefined)
    setView('targets')
  }
  const closeMenu = () => { onClose(); trigger.current?.focus({ preventScroll: true }) }
  const errorMessage = error === 'outcome_unknown' || error === 'idempotency_conflict' ? f.uncertain
    : error === 'stale_preflight' || error === 'binding_mismatch' ? f.source : f.unavailable
  const sourceAllowed = (latest && source?.messageId === messageId) || confirmedOriginal || recoveredFromHistory
  const targetName = nativeTargets.find(item => item.id === (delivery?.delivery.binding.targetId ?? preflight?.targetId ?? target))?.name
  const busyLabel = view === 'flow' ? f[phase] : t.processing
  const preflightState = preflight?.state
  const deliveryState = delivery?.delivery.preparationState
  const receiptLabel = receipt ? status(receipt.state, locale) : status('awaiting', locale)
  const receiptConfirmed = receipt?.state === 'accepted'
  const preparationStatus = delivery?.delivery.expired ? (locale === 'zh' ? '交接包已过期' : 'The handoff package expired')
    : deliveryState === 'needs_input' ? f.missing
    : deliveryState === 'unknown' || delivery?.delivery.error?.code === 'outcome_unknown' ? f.uncertain
    : deliveryState === 'failed' ? f.unavailable : status(deliveryState, locale)
  return <Menu key={view} open={active} onClose={closeMenu} side="right" portal compact dense autoFocus={view === 'targets'}
    anchorRef={trigger} listClassName="pc-handoff-menu"
    anchor={<Tooltip label={t.action} side="bottom"><button ref={trigger} type="button" className="pc-handoff-action"
      aria-label={t.action} aria-haspopup="menu" aria-expanded={active} onClick={() => {
        if (active) closeMenu()
        else { if (view === 'diagnostics') setDiagnostics({}); onOpen() }
      }}>
      <IconShareOutlineRegular /></button></Tooltip>}>
    {view === 'targets' && <>
      <div className="pc-handoff-brand"><PowerContextMark size={15} /><span>PowerContext</span></div>
      {!ports && <p role="status">{t.unavailable}</p>}
      {error && <p role="alert" className="pc-handoff-notice">{errorMessage}</p>}
      {nativeTargets.map(item => {
        const targetInfo = targets.find(candidate => candidate.targetId === item.id)
        const available = !!replySource && latest && !busy && targetInfo?.availability === 'requires_preflight'
        const reason = !replySource ? (error ? t.notAvailable : t.loading) : !latest ? t.boundary : busy ? t.processing
          : targetInfo?.diagnostics === 'available' ? t.unqualifiedShort : t.notAvailable
        return <MenuItemButton key={item.id} disabled={!available} disabledReason={reason} onSelect={() => startPreflight(item.id)}>{item.name}</MenuItemButton>
      })}
      <div className="pc-handoff-menu-footer">
        <button type="button" disabled={busy} onClick={() => { setDiagnostics({}); setView('diagnostics') }}><span aria-hidden="true"><IconCheckCircleOutlineRegular /></span>{t.diagnostics}</button>
        <button type="button" disabled={busy} onClick={() => {
          setView('history'); void run(async signal => setHistory((await api.history(sessionId, signal)).deliveries))
        }}><span aria-hidden="true"><IconClockOutlineRegular /></span>{t.history}</button>
      </div>
    </>}
    {view !== 'targets' && <div ref={focusPanel} tabIndex={-1} className="pc-handoff-panel" onKeyDown={event => {
      if (event.key === 'Tab') event.stopPropagation()
      else if (event.target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)
        && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) event.stopPropagation()
    }}>
      <div className="pc-handoff-panel-header">
        <button type="button" className="pc-handoff-back" aria-label={t.back} title={t.back} disabled={busy && view !== 'flow'} onClick={returnToTargets}><IconChevronLeftOutlineRegular /></button>
        <span>{view === 'diagnostics' ? t.diagnostics : view === 'history' ? t.history : targetName ?? t.target}</span>
        <span className="pc-handoff-header-mark" aria-hidden="true"><PowerContextMark size={14} /></span>
      </div>
      {busy && <div className="pc-handoff-progress" role="status"><span className="pc-handoff-spinner" aria-hidden="true" />{busyLabel}</div>}
      {!ports && <p role="status">{t.unavailable}</p>}
      {error && <p role="alert" className="pc-handoff-notice">{errorMessage}</p>}
      {view === 'diagnostics' && nativeTargets.map(item => {
        const info = targets.find(candidate => candidate.targetId === item.id)
        const result = diagnostics[item.id]
        return <div key={item.id} className="pc-handoff-diagnostic-row"><div className="pc-handoff-row-content">
          <span className="pc-handoff-row-title">{item.name}</span>
          <span role="status" className="pc-handoff-row-meta pc-handoff-diagnostic-status">{
            result?.state === 'checking' || (!result && info?.diagnostics === 'available')
              ? <><span className="pc-handoff-spinner" aria-hidden="true" /><span className="pc-sr-only">{t.checkingInstallation}</span></>
              : result?.state === 'present' ? `${t.installationPresent}: ${result.version}`
              : result?.state === 'missing' ? t.installationMissing : result?.state === 'unsupported' ? t.installationUnsupported
              : result?.state === 'unknown' ? t.installationUnknown : result?.state === 'error' ? t.checkFailed : t.checkUnavailable}</span>
          </div></div>
      })}
      {view === 'flow' && source && <>
        {!sourceAllowed && <p role="alert" className="pc-handoff-notice">{f.source}</p>}
        {boundTarget && boundTarget.availability !== 'requires_preflight' && <p className="pc-handoff-notice">{t.unqualified}</p>}
        {!busy && !preflight && pending.preflights.has(target) && <div className="pc-handoff-actions">
          <button type="button" disabled={!canHandoff(target) || !latest} onClick={() => startPreflight(target)}>{t.check}</button>
        </div>}
        {preflight && !delivery && !busy && <section className="pc-handoff-stage">
          <div className="pc-handoff-stage-heading"><span className="pc-handoff-stage-icon"><IconCheckCircleOutlineRegular /></span>
            <div><h3>{t.preflight}</h3><p>{preflightState === 'unknown' ? f.uncertain
              : preflightState === 'needs_install_consent' ? status(preflight.nextAction ?? 'install_integration', locale)
              : status(preflight.nextAction || preflightState, locale)}</p></div></div>
          <div className="pc-handoff-actions">
            {preflightState === 'needs_install_consent' && <>
              <button className="pc-handoff-emphasis" disabled={!latest} onClick={() => void run(async signal => {
                setPhase('installing')
                const checked = await api.decision(preflight, true, signal)
                setPreflight(checked)
                await prepareDelivery(checked, signal, true)
              })}>{t.yes}</button>
              <button onClick={() => void run(async signal => setPreflight(await api.decision(preflight, false, signal)))}>{t.no}</button>
            </>}
            {preflightState === 'needs_user_action' && preflight.nextAction === 'reselect_source' && <button onClick={() => void run(async signal => {
              setPhase('checking')
              const checked = await api.verify(preflight, true, signal)
              setPreflight(checked); setConfirmedOriginal(checked.state === 'ready')
              await prepareDelivery(checked, signal, true, true)
            })}>{t.original}</button>}
            {preflightState === 'needs_user_action' && preflight.nextAction !== 'reselect_source' && <button disabled={!sourceAllowed} onClick={() => void run(async signal => {
              setPhase('checking')
              const checked = await api.verify(preflight, false, signal)
              setPreflight(checked)
              await prepareDelivery(checked, signal, true, confirmedOriginal)
            })}>{t.verify}</button>}
            {error === 'outcome_unknown' && <button onClick={() => void run(async signal => setPreflight(await api.getPreflight(preflight.preflightId, sessionId, signal)))}>{t.refresh}</button>}
            {(['failed', 'cancelled', 'unknown'].includes(preflight.state) || !sourceAllowed) && <button onClick={() => { clearFlow(); setView('targets') }}>{t.reset}</button>}
            {preflightState === 'ready' && <button className="pc-handoff-emphasis" disabled={!canHandoff(preflight.targetId) || !sourceAllowed}
              onClick={() => void run(signal => prepareDelivery(preflight, signal, false, confirmedOriginal))}>{t.prepare}</button>}
          </div>
        </section>}
        {delivery && (!busy || phase !== 'opening') && <section className="pc-handoff-stage">
          <div className="pc-handoff-stage-heading"><span className="pc-handoff-stage-icon"><IconShareOutlineRegular /></span>
            <div><h3>{attempt?.state === 'opened' ? status('opened', locale) : t.preparation}</h3>
              <p>{attempt?.state === 'opened' ? receiptConfirmed ? f.received : f.waiting : attempt?.state === 'unknown' ? f.uncertain
                : attempt?.state === 'failed' ? f.unavailable : preparationStatus}</p></div></div>
          {deliveryState === 'needs_input' && !manual && <div className="pc-handoff-actions">
            <button disabled={busy} onClick={() => setManual(true)}>{t.manual}</button>
          </div>}
          {deliveryState === 'needs_input' && manual && <div className="pc-handoff-fields">
            <label>{t.disposition}<select disabled={busy || !!pendingManual} value={pendingInput?.disposition ?? disposition}
              onChange={e => setDisposition(e.target.value as typeof disposition)}><option value="">{t.selectDisposition}</option>
              <option value="continuable">{t.continuable}</option><option value="blocked">{t.blocked}</option><option value="complete">{t.complete}</option></select></label>
            <label>{t.objective}<textarea disabled={busy || !!pendingManual} value={pendingInput?.objective ?? objective} onChange={e => setObjective(e.target.value)} /></label>
            <label>{t.state}<textarea disabled={busy || !!pendingManual} value={pendingInput?.state[0]?.text ?? state} onChange={e => setState(e.target.value)} /></label>
            <details><summary>{f.optional}</summary>
              <label>{t.next}<textarea disabled={busy || !!pendingManual} value={pendingInput ? pendingInput.next_action?.text ?? '' : next} onChange={e => setNext(e.target.value)} /></label>
              <label>{t.omissions}<textarea disabled={busy || !!pendingManual} value={pendingInput ? pendingInput.omissions.join('\n') : omissions} onChange={e => setOmissions(e.target.value)} /></label>
              <p>{t.fixedOmission}</p>
            </details>
            <div className="pc-handoff-actions"><button className="pc-handoff-emphasis" disabled={busy || !canHandoff(delivery.delivery.binding.targetId)
              || !sourceAllowed || !preflight || (!pendingInput && (!disposition || !objective.trim() || !state.trim()))} onClick={() => void run(async signal => {
              if (preflight && manualKey && (pendingInput || disposition)) {
                setPhase('preparing')
                const selectedLocale = requestLocale()
                const pending = pendingManual ?? { requestId: id(), preflight, locale: selectedLocale,
                  input: workInput(objective, state, next, omissions, disposition as 'continuable' | 'blocked' | 'complete', selectedLocale) }
                pendingPreparations.current.set(manualKey, pending)
                acceptDelivery(await api.prepare(pending.preflight, pending.requestId, pending.input, delivery.delivery.deliveryId, signal, pending.locale))
                pendingPreparations.current.delete(manualKey)
              }
            })}>{t.submit}</button></div></div>}
          <div className="pc-handoff-actions">
            {delivery.delivery.recoveryActions.includes('create_new_version') && deliveryState !== 'needs_input' && preflight?.state === 'ready' &&
              <button disabled={busy || !canHandoff(delivery.delivery.binding.targetId) || !sourceAllowed} onClick={() => void run(async signal => {
                setPhase('preparing')
                const key = preparationKey(preflight.preflightId, delivery.delivery.deliveryId, 'automatic')
                const pending = pendingPreparations.current.get(key) ?? { requestId: id(), preflight, locale: requestLocale() }
                pendingPreparations.current.set(key, pending)
                acceptDelivery(await api.prepare(pending.preflight, pending.requestId, undefined, delivery.delivery.deliveryId, signal, pending.locale))
                pendingPreparations.current.delete(key); setManual(false)
              })}>{t.newVersion}</button>}
            {deliveryState === 'prepared' && !delivery.delivery.expired && !['opened', 'requested'].includes(attempt?.state ?? '') &&
              <button className="pc-handoff-emphasis" disabled={busy || !canHandoff(delivery.delivery.binding.targetId) || !sourceAllowed}
                onClick={() => void run(signal => launchDelivery({ ...delivery, launchAttempts: attempt ? [attempt] : [] }, signal))}>{attempt ? t.retry : t.open}</button>}
            {!attempt || attempt.state !== 'opened' ? <button aria-label={f.refreshDelivery} disabled={busy} onClick={() => void run(async signal => {
              acceptDelivery(await api.get(delivery.delivery.deliveryId, sessionId, signal), true)
            })}>{t.refresh}</button> : null}
          </div>
          <details className="pc-handoff-details"><summary>{f.details}</summary>
            <div className="pc-handoff-row-meta pc-handoff-break">{f.identifier}: <code>{delivery.delivery.deliveryId}</code></div>
            {delivery.delivery.preview?.objective && <p>{delivery.delivery.preview.objective}</p>}
            {deliveryState === 'prepared' && !delivery.delivery.expired && <div className="pc-handoff-actions">
              <a href={api.exportUrl(delivery.delivery.deliveryId, sessionId)} download>{t.export}</a>
            </div>}
          </details>
        </section>}
        {delivery && attempt && ['opened', 'unknown'].includes(attempt.state) && <section className="pc-handoff-stage pc-handoff-receipt">
          <div className="pc-handoff-stage-heading"><span className="pc-handoff-stage-icon">
            {receiptConfirmed ? <IconCheckCircleOutlineRegular /> : <IconClockOutlineRegular />}</span>
            <div><h3>{receiptLabel}</h3>
              {receiptRead && <p>{t.receiptRead}: {status(receiptRead, locale)}</p>}
              {receipt?.attribution && <p>{receipt.attribution.kind === 'authenticated' ? t.authenticated : t.selfReported}
                {' · '}{t.receiver}: {receipt.attribution.receiver}
                {receipt.attribution.kind === 'authenticated' && <> · {t.principal}: {receipt.attribution.principal.type} · {receipt.attribution.principal.id}</>}</p>}
              {timedOut && (!receipt || receipt.state === 'awaiting') && <p>{t.timeout}</p>}</div></div>
          <div className="pc-handoff-actions">
            <button aria-label={f.refreshReceipt} onClick={() => void readReceipt(delivery.delivery.deliveryId)}>{t.refresh}</button>
          </div>
        </section>}
        <div className="pc-handoff-actions pc-handoff-stop">
          <button onClick={returnToTargets}>{locale === 'zh' ? '更换目标' : 'Change target'}</button>
          {(busy || (preflight && !['failed', 'cancelled', 'unknown'].includes(preflight.state) && attempt?.state !== 'opened')) &&
            <button onClick={stop}>{t.cancel}</button>}
        </div>
      </>}
      {view === 'history' && <div className="pc-handoff-history-list">
        {history?.length === 0 && <p className="pc-handoff-empty">{t.noHistory}</p>}
        {history?.map(item => <div key={item.deliveryId} className="pc-handoff-history-item">
          <div className="pc-handoff-row-title" title={item.preview?.objective ?? item.deliveryId}>{item.preview?.objective ?? t.deliveryId}</div>
          <div className="pc-handoff-row-meta">
            {nativeTargets.find(target => target.id === item.binding.targetId)?.name} · <time dateTime={item.createdAt} title={item.createdAt}>{historyTime(item.createdAt, locale)}</time>
          </div>
          <div className="pc-handoff-row-meta" title={item.deliveryId}>
            {status(item.preparationState, locale)} {item.pinned ? `· ${t.pinned}` : ''} · <code>{item.deliveryId.slice(-8)}</code>
          </div>
          <div className="pc-handoff-history-actions">
          <button disabled={busy} onClick={() => void run(async signal => { acceptDelivery(await api.get(item.deliveryId, sessionId, signal), true); setView('flow') })}>{t.recover}</button>
          <button disabled={busy} onClick={() => void run(async signal => {
            const updated = await api.pin(item.deliveryId, sessionId, !item.pinned, signal)
            setHistory(current => current?.map(row => row.deliveryId === updated.deliveryId ? updated : row))
            if (delivery?.delivery.deliveryId === updated.deliveryId) setDelivery({ ...delivery, delivery: updated })
          })}>{item.pinned ? t.unpin : t.pin}</button>
          {deleteId === item.deliveryId ? <><button disabled={busy} onClick={() => void run(async signal => {
            await api.delete(item.deliveryId, sessionId, signal)
            setHistory(current => current?.filter(row => row.deliveryId !== item.deliveryId)); setDeleteId(undefined)
            pendingLaunches.current.delete(item.deliveryId)
            for (const key of pendingPreparations.current.keys()) {
              const [preflightId, predecessor] = JSON.parse(key)
              if (predecessor === item.deliveryId || (predecessor === null && preflightId === item.binding.preflightId)) {
                pendingPreparations.current.delete(key)
              }
            }
            if (delivery?.delivery.deliveryId === item.deliveryId) clearFlow()
          })}>{t.confirmDelete}</button><button disabled={busy} onClick={() => setDeleteId(undefined)}>{t.keep}</button></>
            : <button disabled={busy} onClick={() => setDeleteId(item.deliveryId)}>{t.delete}</button>}</div>
        </div>)}
      </div>}
    </div>}
  </Menu>
}
