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

// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { HandoffAction } from '../src/ui/handoff/panel.tsx'
import { HandoffHttp, HandoffHttpError } from '../src/ui/handoff/client.ts'
import type { HandoffPorts } from '../src/ui/handoff/client.ts'
import { saveLocale } from '../src/ui/locale.ts'

const source = { sessionId: 's1', messageId: 'm1', throughSeq: 5, cwd: '/work', workspaceObservedAt: '2026-11-13T00:00:00.000Z' }
const ready = { preflightId: 'p1', revision: 2, source, targetId: 'codex-desktop', state: 'ready' }
const delivery = (preparationState: string, id = 'd1') => ({ deliveryId: id, source,
  preparationState, binding: { targetId: 'codex-desktop', preflightId: 'p1' }, recoveryActions: preparationState === 'needs_input' ? ['supply_work_input', 'create_new_version'] : ['export_package'] })
const snapshot = (item: ReturnType<typeof delivery>, attempts: unknown[] = []) => ({ delivery: item, preflight: { ...ready, source: item.source }, launchAttempts: attempts })
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } })
let element: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  localStorage.clear()
  element = document.createElement('div'); document.body.append(element); root = createRoot(element)
})
afterEach(async () => {
  await act(async () => root.unmount())
  element.remove(); vi.unstubAllGlobals(); vi.useRealTimers()
})
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
function button(label: string) {
  const found = [...document.body.querySelectorAll('button')].find(b => b.textContent?.includes(label) || b.getAttribute('aria-label')?.includes(label))
  if (!found) throw new Error(`Missing button ${label}: ${document.body.textContent}`)
  return found
}
async function click(label: string) {
  await act(async () => { button(label).click(); await Promise.resolve() })
  await flush()
}
async function closeMenu() {
  await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
  await flush()
}
async function openHistory() {
  if (![...document.body.querySelectorAll('button')].some(item => item.textContent === '交接记录')) await click('返回')
  await click('交接记录')
  await flush()
}
async function select(index: number, value: string) {
  const item = document.body.querySelectorAll('select')[index]
  await act(async () => { item.value = value; item.dispatchEvent(new Event('change', { bubbles: true })) })
}
async function input(index: number, value: string) {
  const item = document.body.querySelectorAll('textarea')[index]
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(item, value)
    item.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function props(ports: HandoffPorts, id = 'm1') {
  const chat = { timeline: { turnOrder: [1] }, locations: { getTurn: () => ['last'] },
    nodes: { get: () => ({ kind: 'turn-tail', data: { closing: { finalNode: { messageId: 'm1' } } } }) } }
  return { sessionId: 's1', messageId: id, ports, useChat: <T,>(selector: (value: typeof chat) => T) => selector(chat) }
}

it('opens the three native targets in a right-side portal and returns focus on outside dismissal', async () => {
  const fetcher = vi.fn(async () => json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] }))
  vi.stubGlobal('fetch', fetcher)
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  const trigger = button('交接给其他 Agent')
  expect(trigger.textContent).toBe('')
  await click('交接给其他 Agent')
  const menu = document.body.querySelector('[role="menu"]')!
  expect(menu.parentElement).toBe(document.body)
  expect(menu.getAttribute('data-side')).toBe('right')
  expect(menu.getAttribute('data-portal')).toBe('true')
  expect([...menu.querySelectorAll('[role="menuitem"]')].map(item => item.textContent)).toEqual(['Codex Desktop', 'Claude Desktop (Code)', 'Claude Code CLI'])
  expect(document.activeElement?.textContent).toBe('Codex Desktop')
  await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })) })
  expect(document.activeElement?.textContent).toBe('Claude Desktop (Code)')
  await act(async () => { document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })) })
  expect(document.body.querySelector('[role="menu"]')).toBeNull()
  expect(document.activeElement).toBe(trigger)
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('rebinds the toolbar anchor when switching compact secondary views and back', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url) === '/powercontext/targets'
    ? json({ targets: [] }) : json({ deliveries: [] })))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  const firstMenu = document.body.querySelector('[role="menu"]')!
  expect(firstMenu.getAttribute('data-trigger-anchor')).toBe('交接给其他 Agent')
  await click('安装检查')
  const diagnosticsMenu = document.body.querySelector('[role="menu"]')!
  expect(diagnosticsMenu).not.toBe(firstMenu)
  expect(diagnosticsMenu.getAttribute('data-trigger-anchor')).toBe('交接给其他 Agent')
  expect(document.activeElement?.className).toBe('pc-handoff-panel')
  await click('返回')
  const returnedMenu = document.body.querySelector('[role="menu"]')!
  expect(returnedMenu).not.toBe(diagnosticsMenu)
  expect(returnedMenu.getAttribute('data-trigger-anchor')).toBe('交接给其他 Agent')
  expect(document.activeElement?.textContent).toBe('Codex Desktop')
  await click('交接记录')
  expect(document.body.textContent).toContain('此会话没有已保存的交接')
  expect(document.body.querySelector('details')).toBeNull()
  await click('返回')
  expect(document.body.querySelector('[role="menu"]')?.getAttribute('data-trigger-anchor')).toBe('交接给其他 Agent')
})

it('checks installation without allowing unqualified targets to prepare or launch', async () => {
  const ids = ['codex-desktop', 'claude-desktop', 'claude-code-cli']
  const targets = ids.map(targetId => ({ targetId, name: targetId, diagnostics: 'available', availability: 'unavailable', action: 'manual_setup_or_export' }))
  const saved = { ...delivery('prepared', 'saved'), createdAt: '2026-11-12T00:00:00Z', pinned: false }
  const calls: string[] = []
  let codexChecks = 0
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url); calls.push(path)
    if (path === '/powercontext/targets') return json({ targets })
    if (path === '/powercontext/targets/codex-desktop/diagnose') return ++codexChecks === 1
      ? json({ targetId: ids[0], state: 'present', version: '1.2.3' }) : json({ code: 'integration_unavailable' }, 503)
    if (path === '/powercontext/targets/claude-desktop/diagnose') return json({ targetId: ids[1], state: 'missing' })
    if (path === '/powercontext/targets/claude-code-cli/diagnose') return json({ targetId: ids[2], state: 'unknown' })
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/saved?sessionId=s1') return json(snapshot(saved))
    throw new Error(`unexpected request ${path}`)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  expect(button('Codex Desktop').getAttribute('aria-disabled')).toBe('true')
  expect(button('Codex Desktop').title).toContain('尚未通过交接验证')
  expect(document.body.querySelector('a[download]')).toBeNull()
  await click('安装检查')
  expect(document.body.querySelectorAll('.pc-handoff-diagnostic-row button')).toHaveLength(0)
  expect(document.body.textContent).toContain('已安装，版本: 1.2.3')
  expect(document.body.textContent).toContain('未检测到安装')
  expect(document.body.textContent).toContain('无法确定安装状态')
  await click('返回'); await click('安装检查')
  expect(document.body.textContent).not.toContain('已安装，版本: 1.2.3')
  expect(document.body.textContent).toContain('检查失败')
  await openHistory()
  await flush(); await click('查看交付')
  expect(button('打开目标').disabled).toBe(true)
  expect(document.body.querySelector('a[download]')?.getAttribute('href')).toContain('/deliveries/saved/export')
  expect(calls.filter(path => path.includes('/diagnose'))).toHaveLength(6)
  expect(calls.some(path => path.includes('/preflights') || path.endsWith('/launch'))).toBe(false)
})

it('shows the English unsupported diagnostic without claiming a verified handoff', async () => {
  localStorage.setItem('powercontext.dsh.locale', 'en')
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url) === '/powercontext/targets'
    ? json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', diagnostics: 'available', availability: 'unavailable', action: 'manual_setup_or_export' }] })
    : json({ targetId: 'codex-desktop', state: 'unsupported' })))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('Hand off to another agent'); await click('Installation checks')
  expect(document.body.textContent).toContain('Installation check is not supported on this platform')
  expect(document.body.textContent).not.toContain('Current version not yet validated for handoff')
  await click('Back'); expect(button('Codex Desktop').getAttribute('aria-disabled')).toBe('true')
})

it('runs available installation checks in parallel and ignores results from closed or earlier views', async () => {
  const requests: Array<{ path: string; signal: AbortSignal; resolve(value: Response): void }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [
      { targetId: 'codex-desktop', diagnostics: 'available', availability: 'unavailable' },
      { targetId: 'claude-desktop', diagnostics: 'available', availability: 'unavailable' },
      { targetId: 'claude-code-cli', diagnostics: 'unavailable', availability: 'unavailable' },
    ] })
    if (path.endsWith('/diagnose')) return await new Promise<Response>(resolve => requests.push({ path, signal: init!.signal as AbortSignal, resolve }))
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('安装检查')
  expect(requests.map(item => item.path)).toEqual(['/powercontext/targets/codex-desktop/diagnose', '/powercontext/targets/claude-desktop/diagnose'])
  expect(document.body.querySelectorAll('.pc-handoff-diagnostic-status .pc-handoff-spinner')).toHaveLength(2)
  expect(document.body.textContent).toContain('检查不可用')
  await act(async () => { requests[0].resolve(json({ targetId: 'codex-desktop', state: 'present', version: '1.0' })) }); await flush()
  expect(document.body.textContent).toContain('已安装，版本: 1.0')
  expect(document.body.querySelectorAll('.pc-handoff-diagnostic-status .pc-handoff-spinner')).toHaveLength(1)
  expect(button('返回').disabled).toBe(false)
  await click('返回')
  expect(requests[0].signal.aborted && requests[1].signal.aborted).toBe(true)
  await click('安装检查')
  expect(requests).toHaveLength(4)
  await act(async () => { requests[1].resolve(json({ targetId: 'claude-desktop', state: 'present', version: 'old' })) }); await flush()
  expect(document.body.textContent).not.toContain('old')
  expect(document.body.querySelectorAll('.pc-handoff-diagnostic-status .pc-handoff-spinner')).toHaveLength(2)
  await closeMenu()
  expect(requests[2].signal.aborted && requests[3].signal.aborted).toBe(true)
  await click('交接给其他 Agent')
  expect(requests).toHaveLength(6)
  await act(async () => {
    requests[2].resolve(json({ targetId: 'codex-desktop', state: 'present', version: 'stale' }))
    requests[3].resolve(json({ targetId: 'claude-desktop', state: 'missing' }))
    requests[4].resolve(json({ code: 'integration_unavailable' }, 503))
    requests[5].resolve(json({ targetId: 'claude-desktop', state: 'unsupported' }))
  }); await flush()
  expect(document.body.textContent).not.toContain('stale')
  expect(document.body.textContent).toContain('检查失败')
  expect(document.body.textContent).toContain('当前平台不支持检查')
  expect(document.body.querySelectorAll('.pc-handoff-diagnostic-status .pc-handoff-spinner')).toHaveLength(0)
})

it('preflights, prepares and opens once from the target selection without showing a form', async () => {
  const operations: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path.endsWith('/targets')) return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight' }] })
    operations.push(path)
    const body = JSON.parse(String(init?.body))
    if (path.endsWith('/preflights')) { expect(body.source).toEqual(source); return json(ready) }
    if (path.endsWith('/deliveries')) {
      expect(body).toMatchObject({ source, preflightId: ready.preflightId, expectedRevision: ready.revision, locale: 'zh-CN' })
      expect(body).not.toHaveProperty('workInput')
      return json(snapshot(delivery('prepared')))
    }
    if (path.endsWith('/d1/launch')) {
      expect(body).toMatchObject({ sessionId: 's1', targetId: 'codex-desktop', locale: 'zh-CN' })
      expect(body).not.toHaveProperty('retryOf')
      return json({ launchAttemptId: 'l1', state: 'opened', createdAt: new Date().toISOString() })
    }
    throw new Error(path)
  }))
  await act(async () => root.render(React.createElement(HandoffAction, props({ resolveBoundary: async () => source }))))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  expect(operations).toEqual(['/powercontext/preflights', '/powercontext/deliveries', '/powercontext/deliveries/d1/launch'])
  expect(document.body.querySelector('textarea')).toBeNull()
  expect(document.body.textContent).toContain('发送预填草稿')
  await closeMenu(); await click('交接给其他 Agent')
  expect(operations).toHaveLength(3)
})

it('does not auto-open if a cancelled preparation returns a saved package late', async () => {
  let complete!: (response: Response) => void
  const launched = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url)
    if (path.endsWith('/targets')) return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight' }] })
    if (path.endsWith('/preflights')) return json(ready)
    if (path.endsWith('/deliveries')) return new Promise<Response>(resolve => { complete = resolve })
    launched()
    throw new Error(path)
  }))
  await act(async () => root.render(React.createElement(HandoffAction, props({ resolveBoundary: async () => source }))))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('取消交接')
  await act(async () => complete(json(snapshot(delivery('prepared')))))
  expect(launched).not.toHaveBeenCalled()
  expect(button('打开目标').disabled).toBe(false)
})

it('can change targets during preparation and after launch without deleting or reopening an earlier delivery', async () => {
  const boundary = { ...source, sessionId: 'target-switch' }
  const ids = ['codex-desktop', 'claude-desktop', 'claude-code-cli']
  const checked: string[] = []
  const launches: Array<{ id: string; locale: string }> = []
  let firstSignal: AbortSignal | undefined
  let complete!: () => void
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path.endsWith('/targets')) return json({ targets: ids.map(targetId => ({ targetId, availability: 'requires_preflight' })) })
    const body = JSON.parse(String(init?.body))
    if (path.endsWith('/preflights')) {
      checked.push(body.targetId)
      expect(body.source).toEqual(boundary)
      return json({ ...ready, source: boundary, targetId: body.targetId, preflightId: body.targetId })
    }
    if (path.endsWith('/deliveries')) {
      const item = { ...delivery('prepared', body.preflightId), source: boundary,
        binding: { targetId: body.preflightId, preflightId: body.preflightId } }
      const response = () => json({ ...snapshot(item), preflight: { ...ready, source: boundary, ...item.binding } })
      if (body.preflightId === 'codex-desktop') {
        firstSignal = init!.signal as AbortSignal
        return new Promise<Response>(resolve => { complete = () => resolve(response()) })
      }
      return response()
    }
    if (path.endsWith('/launch')) {
      launches.push({ id: body.targetId, locale: body.locale })
      return json({ launchAttemptId: body.targetId, state: 'opened', createdAt: new Date().toISOString() })
    }
    throw new Error(path)
  }))
  await act(async () => root.render(React.createElement(HandoffAction, {
    ...props({ resolveBoundary: async () => boundary, readReceipt: async () => { throw new Error('unused') } }),
    sessionId: boundary.sessionId,
  })))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  await click('更换目标')
  expect(firstSignal?.aborted).toBe(true)
  await act(async () => complete())
  for (const name of ['Codex Desktop', 'Claude Desktop (Code)', 'Claude Code CLI']) {
    expect(button(name).getAttribute('aria-disabled')).not.toBe('true')
  }
  expect(launches).toEqual([])
  // A preference changed while the slot is already mounted applies to the next launch.
  await act(async () => { saveLocale('en') })
  await click('Claude Desktop (Code)')
  await click('Change target'); await click('Claude Code CLI')
  expect(checked).toEqual(ids)
  expect(launches).toEqual([{ id: 'claude-desktop', locale: 'en' }, { id: 'claude-code-cli', locale: 'en' }])
})

it('uses the selected boundary, preserves a manual handoff, and keeps an accepted receipt when a later read fails', async () => {
  const sent: Array<{ path: string; body: Record<string, unknown> }> = []
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
    sent.push({ path, body })
    if (path === '/powercontext/preflights') return json(ready)
    if (path === '/powercontext/deliveries') return json(body.workInput ? snapshot(delivery('prepared', 'd2')) : snapshot(delivery('needs_input')))
    if (path === '/powercontext/deliveries/d2/launch') return json({ launchAttemptId: 'l1', state: 'opened', createdAt: new Date().toISOString() })
    throw new Error(`unexpected request ${path}`)
  })
  vi.stubGlobal('fetch', fetcher)
  const receipt = vi.fn().mockResolvedValueOnce({ deliveryId: 'd2', state: 'accepted', readState: 'verified',
    observedAt: new Date().toISOString(), attribution: { kind: 'self_reported', receiver: 'receiver-a' } })
    .mockRejectedValue(new HandoffHttpError('integration_unavailable', 503))
  const ports = { resolveBoundary: vi.fn(async () => source), readReceipt: receipt }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  await click('Codex Desktop'); await click('手动填写')
  for (const field of [document.body.querySelector('textarea')!, document.body.querySelector('select')!]) {
    field.focus()
    for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End']) {
      const arrow = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
      await act(async () => { field.dispatchEvent(arrow) })
      expect(arrow.defaultPrevented).toBe(false)
      expect(document.activeElement).toBe(field)
    }
    for (const shiftKey of [false, true]) {
      const tab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true })
      await act(async () => { field.dispatchEvent(tab) })
      expect(tab.defaultPrevented).toBe(false)
      expect(document.body.querySelector('[role="menu"]')).not.toBeNull()
    }
  }
  await select(0, 'blocked'); await input(0, 'Fix bug'); await input(1, 'Checks failed'); await input(2, 'Investigate'); await input(3, 'Old state unknown')
  await click('使用填写内容准备')
  const request = sent.filter(x => x.path === '/powercontext/deliveries').at(-1)!.body
  expect(request).toMatchObject({ source, preflightId: 'p1', expectedRevision: 2, supersedesDeliveryId: 'd1',
    workInput: { objective: 'Fix bug', disposition: 'blocked', state: [{ text: 'Checks failed', basis: 'declared' }] } })
  await click('打开目标'); await flush()
  expect(document.body.textContent).toContain('接收方已确认')
  expect(document.body.textContent).toContain('接收方自行报告')
  const refresh = [...document.body.querySelectorAll('button')].filter(b => b.textContent === '刷新状态').at(-1)!
  await act(async () => { refresh.click(); await Promise.resolve() }); await flush()
  expect(document.body.textContent).toContain('接收方已确认')
  expect(document.body.textContent).toContain('服务不可用')
  await closeMenu()
  expect(document.body.querySelector('[role="menu"]')).toBeNull()
  await click('交接给其他 Agent')
  expect(document.body.textContent).toContain('接收方已确认')
})

it('resolves boundaries and reads receipt through the exact owner-local HTTP routes', async () => {
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    if (String(url).startsWith('/powercontext/boundaries?')) return json({ source })
    return json({ deliveryId: 'd1', state: 'awaiting', readState: 'not_found', observedAt: '2026-11-13T00:00:00.000Z' })
  })
  const api = new HandoffHttp(fetcher as typeof fetch)
  expect(await api.resolveBoundary('s1', 'm1')).toEqual(source)
  expect((await api.readReceipt('d1', 's1')).readState).toBe('not_found')
  expect(fetcher.mock.calls.map(call => String(call[0]))).toEqual([
    '/powercontext/boundaries?sessionId=s1&messageId=m1', '/powercontext/deliveries/d1/receipt?sessionId=s1',
  ])
  await expect(api.resolveBoundary('s1', 'other')).rejects.toThrow('binding_mismatch')
})

it('lists session history and requires an explicit deletion confirmation', async () => {
  const previous = { ...delivery('prepared', 'previous'), source: { ...source, messageId: 'old' },
    createdAt: '2026-11-12T00:00:00Z', pinned: false, preview: { objective: 'Previous work' } }
  let stored = previous
  const calls: Array<{ path: string; body?: any }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ path, body })
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'claude-code-cli', availability: 'requires_preflight' }] })
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [stored] })
    if (path === '/powercontext/deliveries/previous?sessionId=s1') return json(snapshot(previous))
    if (path === '/powercontext/deliveries/previous/pin') { stored = { ...stored, pinned: body.pinned }; return json(stored) }
    if (path === '/powercontext/deliveries/previous/delete') return json({ deleted: true })
    if (path === '/powercontext/preflights') return json({ ...ready, targetId: 'claude-code-cli', preflightId: 'after-delete', state: 'needs_user_action' })
    throw new Error(`unexpected request ${path}`)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unavailable') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  await openHistory()
  await flush()
  expect(document.body.textContent).toContain('Previous work')
  await click('固定')
  expect(calls.find(call => call.path.endsWith('/pin'))?.body).toEqual({ sessionId: 's1', pinned: true })
  await click('取消固定')
  expect(calls.filter(call => call.path.endsWith('/pin')).at(-1)?.body).toEqual({ sessionId: 's1', pinned: false })
  await click('查看交付')
  expect(document.body.textContent).toContain('previous')
  await openHistory()
  await click('删除')
  expect(calls.some(call => call.path.endsWith('/delete'))).toBe(false)
  await click('确认删除')
  expect(calls.find(call => call.path.endsWith('/delete'))?.body).toEqual({ sessionId: 's1' })
  await click('返回'); await click('Claude Code CLI')
  expect(calls.find(call => call.path.endsWith('/preflights'))?.body).toMatchObject({ source, targetId: 'claude-code-cli' })
})

it('pauses receipt polling while hidden and after five minutes, but permits manual refresh', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-13T00:00:00Z'))
  let visible = true
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visible ? 'visible' : 'hidden')
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => !visible)
  const saved = { ...delivery('prepared', 'd1'), createdAt: new Date().toISOString(), pinned: false }
  const attempt = { launchAttemptId: 'l1', state: 'opened', createdAt: new Date().toISOString() }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [] })
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/d1?sessionId=s1') return json(snapshot(saved, [attempt]))
    throw new Error(path)
  }))
  const reader = vi.fn(async () => ({ deliveryId: 'd1', state: 'awaiting' as const, readState: 'not_found' as const, observedAt: new Date().toISOString() }))
  const ports = { resolveBoundary: async () => source, readReceipt: reader }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  await openHistory()
  await flush(); await click('查看交付')
  expect(reader).toHaveBeenCalledTimes(1)
  await act(async () => { visible = false; document.dispatchEvent(new Event('visibilitychange')); await vi.advanceTimersByTimeAsync(60_000) })
  expect(reader).toHaveBeenCalledTimes(1)
  await act(async () => { visible = true; document.dispatchEvent(new Event('visibilitychange')); await Promise.resolve() })
  expect(reader).toHaveBeenCalledTimes(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(240_000) })
  expect(document.body.textContent).toContain('五分钟内尚未确认')
  const count = reader.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
  expect(reader).toHaveBeenCalledTimes(count)
  const refresh = [...document.body.querySelectorAll('button')].filter(b => b.textContent === '刷新状态').at(-1)!
  await act(async () => { refresh.click(); await Promise.resolve() })
  expect(reader).toHaveBeenCalledTimes(count + 1)
})

it('can stop a busy request without claiming cancellation, and explicitly retries the same preflight ID', async () => {
  const requests: Array<{ body: any; signal: AbortSignal }> = []
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url) === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (String(url) === '/powercontext/preflights') {
      const signal = init!.signal as AbortSignal
      requests.push({ body: JSON.parse(String(init!.body)), signal })
      if (requests.length === 1) return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      return json(ready)
    }
    throw new Error(String(url))
  })
  vi.stubGlobal('fetch', fetcher)
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  expect(button('取消交接').disabled).toBe(false)
  await click('取消交接')
  expect(requests[0].signal.aborted).toBe(true)
  expect(requests).toHaveLength(1)
  expect(document.body.textContent).toContain('结果还未确认')
  await closeMenu(); await click('交接给其他 Agent')
  expect(requests).toHaveLength(1)
  expect(document.body.textContent).toContain('结果还未确认')
  await click('重新检查目标')
  expect(requests).toHaveLength(2)
  expect(requests[1].body.requestId).toBe(requests[0].body.requestId)
})

it('declines installation without sending installation consent', async () => {
  const calls: Array<{ path: string; body: any }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    const body = JSON.parse(String(init?.body ?? '{}')); calls.push({ path, body })
    if (path === '/powercontext/preflights') return json({ ...ready, state: 'needs_install_consent' })
    if (path === '/powercontext/preflights/p1/install') return json({ ...ready, revision: 3, state: 'cancelled' })
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('不安装并取消')
  expect(calls.find(call => call.path.endsWith('/install'))?.body).toEqual({ sessionId: 's1', expectedRevision: 2, consent: false })
  expect(document.body.textContent).toContain('已取消')
})

it('closing the panel does not abort an in-flight check and reopening retains its result', async () => {
  let finish!: (value: Response) => void
  let requestSignal!: AbortSignal
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url) === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (String(url) === '/powercontext/preflights') {
      requestSignal = init!.signal as AbortSignal
      return await new Promise<Response>(resolve => { finish = resolve })
    }
    throw new Error(String(url))
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  await closeMenu()
  expect(requestSignal.aborted).toBe(false)
  await act(async () => { finish(json(ready)); await Promise.resolve() }); await flush()
  await click('交接给其他 Agent')
  expect(document.body.textContent).toContain('已就绪')
})

it('shows the authenticated principal from a validated receipt', async () => {
  const saved = { ...delivery('prepared', 'd1'), createdAt: new Date().toISOString(), pinned: false }
  const attempt = { launchAttemptId: 'l1', state: 'opened', createdAt: new Date().toISOString() }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [] })
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/d1?sessionId=s1') return json(snapshot(saved, [attempt]))
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => ({
    deliveryId: 'd1', state: 'accepted', readState: 'stale', observedAt: new Date().toISOString(),
    attribution: { kind: 'authenticated', receiver: 'receiver-a', principal: { type: 'user', id: 'receiver-a' } },
  }) }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  await openHistory()
  await flush(); await click('查看交付'); await flush()
  expect(document.body.textContent).toContain('历史确认，未重新查询')
  expect(document.body.textContent).toContain('接收方已确认')
  expect(document.body.textContent).toContain('已认证的接收方')
  expect(document.body.textContent).toContain('user · receiver-a')
})

it('only starts installation after an explicit affirmative decision', async () => {
  const calls: Array<{ path: string; body: any }> = []
  const installing = { ...ready, preflightId: 'install-preflight' }
  const installed = { ...installing, revision: 3 }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    const body = JSON.parse(String(init?.body ?? '{}')); calls.push({ path, body })
    if (path === '/powercontext/preflights') return json({ ...installing, state: 'needs_install_consent' })
    if (path === '/powercontext/preflights/install-preflight/install') return json(installed)
    if (path === '/powercontext/deliveries') {
      const item = delivery('prepared', 'after-install')
      return json({ ...snapshot({ ...item, binding: { ...item.binding, preflightId: installed.preflightId } }), preflight: installed })
    }
    if (path === '/powercontext/deliveries/after-install/launch') return json({ launchAttemptId: 'l1', state: 'opened', createdAt: new Date().toISOString() })
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  expect(calls.map(call => call.path)).toEqual(['/powercontext/preflights'])
  await click('同意安装')
  expect(calls.find(call => call.path.endsWith('/install'))?.body).toEqual({ sessionId: 's1', expectedRevision: 2, consent: true })
  expect(calls.map(call => call.path)).toEqual(['/powercontext/preflights', '/powercontext/preflights/install-preflight/install', '/powercontext/deliveries', '/powercontext/deliveries/after-install/launch'])
  expect(calls[2].body.expectedRevision).toBe(3)
  expect(document.body.textContent).toContain('发送预填草稿')
})

it('supports English labels, keyboard dismissal, and does not expose the action on an older reply', async () => {
  localStorage.setItem('powercontext.dsh.locale', 'en')
  vi.stubGlobal('fetch', vi.fn(async () => json({ targets: [] })))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports, 'old'))))
  expect(document.body.querySelector('button')).toBeNull()
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  const trigger = button('Hand off to another agent'); trigger.focus()
  await click('Hand off to another agent')
  expect(document.activeElement?.textContent).toBe('Codex Desktop')
  await act(async () => { document.body.querySelector('[role="menu"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
  expect(document.body.querySelector('[role="menu"]')).toBeNull()
  expect(document.activeElement).toBe(trigger)
})

it('isolates the panel when a mounted slot is rebound to a different session and message', async () => {
  let resolveOld!: (value: typeof source) => void
  const second = { ...source, sessionId: 's2', messageId: 'm2', cwd: '/other', throughSeq: 9 }
  const sent: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') { const body = JSON.parse(String(init!.body)); sent.push(body); return json({ ...ready, source: second }) }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async session => session === 's1'
    ? await new Promise<typeof source>(resolve => { resolveOld = resolve }) : second,
  readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  const secondProps = { ...props(ports, 'm2'), sessionId: 's2',
    useChat: <T,>(selector: (value: any) => T) => selector({ timeline: { turnOrder: [1] }, locations: { getTurn: () => ['last'] },
      nodes: { get: () => ({ kind: 'turn-tail', data: { closing: { finalNode: { messageId: 'm2' } } } }) } }) }
  await act(async () => root.render(React.createElement(HandoffAction, secondProps)))
  expect(document.body.querySelector('[role="menu"]')).toBeNull()
  await click('交接给其他 Agent')
  await act(async () => { resolveOld(source); await Promise.resolve() }); await flush()
  await click('Codex Desktop')
  expect(sent).toHaveLength(1)
  expect(sent[0].source).toEqual(second)
})

it('retries an interrupted preparation with the same identity and no automatic resend', async () => {
  const calls: Array<{ body: any; signal: AbortSignal }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(ready)
    if (path === '/powercontext/deliveries') {
      const signal = init!.signal as AbortSignal
      calls.push({ body: JSON.parse(String(init!.body)), signal })
      if (calls.length === 1) return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      return json(snapshot(delivery('needs_input')))
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  await click('取消交接')
  expect(calls).toHaveLength(1)
  expect(calls[0].signal.aborted).toBe(true)
  await click('整理交接')
  expect(calls).toHaveLength(2)
  expect(calls[1].body).toEqual(calls[0].body)
})

it('uses a fresh request ID only after recovering the exact unknown launch attempt', async () => {
  const launches: Array<{ body: any; signal: AbortSignal }> = []
  const saved = { ...delivery('prepared'), createdAt: '2026-11-13T00:00:00Z', pinned: false }
  const unknown = { launchAttemptId: 'l1', state: 'unknown', createdAt: new Date().toISOString() }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(ready)
    if (path === '/powercontext/deliveries') return json(snapshot(saved))
    if (path === '/powercontext/deliveries/d1?sessionId=s1') return json(snapshot(saved, [unknown]))
    if (path === '/powercontext/deliveries/d1/launch') {
      const signal = init!.signal as AbortSignal
      launches.push({ body: JSON.parse(String(init!.body)), signal })
      if (launches.length === 1) return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      return json({ launchAttemptId: 'l2', state: 'opened', createdAt: new Date().toISOString() })
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('取消交接')
  expect(launches).toHaveLength(1)
  const deliveryRefresh = button('刷新交付状态')
  await act(async () => { deliveryRefresh.click(); await Promise.resolve() }); await flush()
  expect(launches).toHaveLength(1)
  await click('明确重试打开')
  expect(launches).toHaveLength(2)
  expect(launches[1].body.retryOf).toBe('l1')
  expect(launches[1].body.requestId).not.toBe(launches[0].body.requestId)
})

it('recovers an exact delivery from history after the slot unmounts and remounts', async () => {
  const saved = { ...delivery('prepared', 'saved'), createdAt: '2026-11-13T00:00:00Z', pinned: true, preview: { objective: 'Saved work' } }
  let historyReads = 0
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [] })
    if (path === '/powercontext/deliveries?sessionId=s1') { historyReads++; return json({ deliveries: [saved] }) }
    if (path === '/powercontext/deliveries/saved?sessionId=s1') return json(snapshot(saved))
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  const showHistory = async () => {
    await click('交接给其他 Agent')
    await openHistory()
    await flush(); await click('查看交付')
    expect(document.body.textContent).toContain('saved')
  }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await showHistory()
  await act(async () => root.render(null))
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await showHistory()
  expect(historyReads).toBeGreaterThanOrEqual(2)
})

it('keeps the retry request ID if recovery still shows only the prior launch attempt', async () => {
  const launches: any[] = []
  const saved = { ...delivery('prepared'), createdAt: '2026-11-13T00:00:00Z', pinned: false }
  const previous = { launchAttemptId: 'l1', state: 'unknown', createdAt: new Date().toISOString() }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/d1?sessionId=s1') return json(snapshot(saved, [previous]))
    if (path === '/powercontext/deliveries/d1/launch') {
      const signal = init!.signal as AbortSignal
      launches.push(JSON.parse(String(init!.body)))
      if (launches.length === 1) return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      return json({ launchAttemptId: 'l2', state: 'opened', createdAt: new Date().toISOString() })
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent')
  await openHistory()
  await flush(); await click('查看交付'); await click('明确重试打开'); await click('取消交接')
  const deliveryRefresh = button('刷新交付状态')
  await act(async () => { deliveryRefresh.click(); await Promise.resolve() }); await flush()
  expect(launches).toHaveLength(1)
  await click('明确重试打开')
  expect(launches).toHaveLength(2)
  expect(launches[1]).toEqual(launches[0])
})

it('recovers a delivery from history after aborting preparation before its ID was returned', async () => {
  const saved = { ...delivery('prepared', 'saved-after-abort'), createdAt: '2026-11-13T00:00:00Z', pinned: false }
  let prepares = 0
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(ready)
    if (path === '/powercontext/deliveries') {
      prepares++
      const signal = init!.signal as AbortSignal
      return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
    }
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/saved-after-abort?sessionId=s1') return json(snapshot(saved))
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('取消交接')
  await openHistory()
  await flush(); await click('查看交付')
  expect(document.body.textContent).toContain('saved-after-abort')
  expect(prepares).toBe(1)
})

it('allows a manual successor after recovering a needs-input delivery created before an interrupted response', async () => {
  const saved = { ...delivery('needs_input', 'saved-needs-input'), createdAt: '2026-11-13T00:00:00Z', pinned: false }
  const requests: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(ready)
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/saved-needs-input?sessionId=s1') return json(snapshot(saved))
    if (path === '/powercontext/deliveries') {
      requests.push(JSON.parse(String(init!.body)))
      if (requests.length === 1) {
        const signal = init!.signal as AbortSignal
        return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      }
      return json(snapshot(delivery('prepared', 'successor')))
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('取消交接')
  await openHistory()
  await flush(); await click('查看交付'); await click('手动填写')
  expect(document.body.querySelector('textarea')?.disabled).toBe(false)
  await select(0, 'blocked'); await input(0, 'Original goal'); await input(1, 'Known issue'); await click('使用填写内容准备')
  expect(requests).toHaveLength(2)
  expect(requests[1].supersedesDeliveryId).toBe('saved-needs-input')
  expect(requests[1].requestId).not.toBe(requests[0].requestId)
})

it('isolates an uncertain launch request from another delivery in the same session', async () => {
  const localReady = { ...ready, preflightId: 'p-isolated-launch' }
  const launches: Array<{ path: string; body: any }> = []
  const d1 = { ...delivery('prepared', 'isol-d1'), binding: { targetId: 'codex-desktop', preflightId: localReady.preflightId }, createdAt: '2026-11-13T00:00:00Z', pinned: false }
  const d2 = { ...delivery('prepared', 'isol-d2'), binding: { targetId: 'codex-desktop', preflightId: localReady.preflightId }, createdAt: '2026-11-13T00:00:00Z', pinned: false }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(localReady)
    if (path === '/powercontext/deliveries') return json({ ...snapshot(d1), preflight: localReady })
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [d2, d1] })
    if (path === '/powercontext/deliveries/isol-d1?sessionId=s1') return json({ ...snapshot(d1), preflight: localReady })
    if (path === '/powercontext/deliveries/isol-d2?sessionId=s1') return json({ ...snapshot(d2), preflight: localReady })
    if (path.endsWith('/launch')) {
      const signal = init!.signal as AbortSignal
      launches.push({ path, body: JSON.parse(String(init!.body)) })
      if (launches.length === 1) return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      return json({ launchAttemptId: `attempt-${launches.length}`, state: 'opened', createdAt: new Date().toISOString() })
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop')
  expect(launches.map(item => item.path)).toEqual(['/powercontext/deliveries/isol-d1/launch'])
  await click('取消交接')
  await openHistory(); await flush()
  const selectHistory = async (id: string) => {
    if (!document.body.querySelector('.pc-handoff-history-item')) await openHistory()
    const card = [...document.body.querySelectorAll('.pc-handoff-history-item')].find(item => item.querySelector('code')?.textContent === id)!
    await act(async () => { [...card.querySelectorAll('button')].find(item => item.textContent === '查看交付')!.click(); await Promise.resolve() }); await flush()
  }
  await selectHistory('isol-d2'); await click('打开目标')
  await selectHistory('isol-d1'); await click('打开目标')
  expect(launches.map(item => item.path)).toEqual(['/powercontext/deliveries/isol-d1/launch', '/powercontext/deliveries/isol-d2/launch', '/powercontext/deliveries/isol-d1/launch'])
  expect(launches[1].body.requestId).not.toBe(launches[0].body.requestId)
  expect(launches[2].body).toEqual(launches[0].body)
})

it('keeps an interrupted manual payload and request ID bound to its original delivery after switching history', async () => {
  const submissions: any[] = []
  const d1 = { ...delivery('needs_input', 'd1'), createdAt: '2026-11-13T00:00:00Z', pinned: false }
  const d2 = { ...delivery('needs_input', 'd2'), createdAt: '2026-11-13T00:00:00Z', pinned: false }
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(ready)
    if (path === '/powercontext/deliveries?sessionId=s1') return json({ deliveries: [d2, d1] })
    if (path === '/powercontext/deliveries/d1?sessionId=s1') return json(snapshot(d1))
    if (path === '/powercontext/deliveries/d2?sessionId=s1') return json(snapshot(d2))
    if (path === '/powercontext/deliveries') {
      const body = JSON.parse(String(init!.body))
      if (!body.workInput) return json(snapshot(d1))
      submissions.push(body)
      if (submissions.length === 1) {
        const signal = init!.signal as AbortSignal
        return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      }
      return json(snapshot(delivery('prepared', 'successor')))
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => source, readReceipt: async () => { throw new Error('unused') } }
  await act(async () => root.render(React.createElement(HandoffAction, props(ports))))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('手动填写')
  await select(0, 'blocked'); await input(0, 'First objective'); await input(1, 'First status'); await click('使用填写内容准备'); await click('取消交接')
  await openHistory(); await flush()
  const selectHistory = async (id: string) => {
    if (!document.body.querySelector('.pc-handoff-history-item')) await openHistory()
    const card = [...document.body.querySelectorAll('.pc-handoff-history-item')].find(item => item.textContent?.includes(id))!
    await act(async () => { [...card.querySelectorAll('button')].find(item => item.textContent === '查看交付')!.click(); await Promise.resolve() }); await flush()
  }
  await selectHistory('d2'); await click('手动填写'); await input(0, 'Different objective'); await input(1, 'Different status')
  await selectHistory('d1')
  expect(document.body.querySelectorAll('textarea')[0].value).toBe('First objective')
  expect(document.body.querySelectorAll('textarea')[0].disabled).toBe(true)
  await click('使用填写内容准备')
  expect(submissions).toHaveLength(2)
  expect(submissions[1]).toEqual(submissions[0])
})

it('retains an interrupted preflight request ID across component remounts', async () => {
  const boundary = { ...source, sessionId: 'remount-session', messageId: 'remount-message' }
  const calls: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') {
      calls.push(JSON.parse(String(init!.body)))
      if (calls.length === 1) {
        const signal = init!.signal as AbortSignal
        return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      }
      return json({ ...ready, source: boundary })
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => boundary, readReceipt: async () => { throw new Error('unused') } }
  const slot = { ...props(ports, 'remount-message'), sessionId: 'remount-session',
    useChat: <T,>(selector: (value: any) => T) => selector({ timeline: { turnOrder: [1] }, locations: { getTurn: () => ['last'] },
      nodes: { get: () => ({ kind: 'turn-tail', data: { closing: { finalNode: { messageId: 'remount-message' } } } }) } }) }
  await act(async () => root.render(React.createElement(HandoffAction, slot)))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('取消交接')
  await act(async () => root.render(null))
  await act(async () => root.render(React.createElement(HandoffAction, slot)))
  await click('交接给其他 Agent')
  await click('Codex Desktop')
  expect(calls).toHaveLength(2)
  expect(calls[1]).toEqual(calls[0])
})

it('restores an interrupted manual draft and its request identity after remount and history recovery', async () => {
  const boundary = { ...source, sessionId: 'draft-session', messageId: 'draft-message' }
  const draftReady = { ...ready, source: boundary }
  const saved = { ...delivery('needs_input', 'draft-delivery'), source: boundary, createdAt: '2026-11-13T00:00:00Z', pinned: false }
  const draftSnapshot = (item: typeof saved) => ({ delivery: item, preflight: draftReady, launchAttempts: [] })
  const writes: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    if (path === '/powercontext/targets') return json({ targets: [{ targetId: 'codex-desktop', name: 'Codex', availability: 'requires_preflight', action: 'preflight' }] })
    if (path === '/powercontext/preflights') return json(draftReady)
    if (path === '/powercontext/deliveries?sessionId=draft-session') return json({ deliveries: [saved] })
    if (path === '/powercontext/deliveries/draft-delivery?sessionId=draft-session') return json(draftSnapshot(saved))
    if (path === '/powercontext/deliveries') {
      const body = JSON.parse(String(init!.body))
      if (!body.workInput) return json(draftSnapshot(saved))
      writes.push(body)
      if (writes.length === 1) {
        const signal = init!.signal as AbortSignal
        return await new Promise<Response>((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      }
      return json(draftSnapshot({ ...saved, preparationState: 'prepared', deliveryId: 'draft-successor' }))
    }
    throw new Error(path)
  }))
  const ports: HandoffPorts = { resolveBoundary: async () => boundary, readReceipt: async () => { throw new Error('unused') } }
  const slot = { ...props(ports, 'draft-message'), sessionId: 'draft-session',
    useChat: <T,>(selector: (value: any) => T) => selector({ timeline: { turnOrder: [1] }, locations: { getTurn: () => ['last'] },
      nodes: { get: () => ({ kind: 'turn-tail', data: { closing: { finalNode: { messageId: 'draft-message' } } } }) } }) }
  await act(async () => root.render(React.createElement(HandoffAction, slot)))
  await click('交接给其他 Agent'); await click('Codex Desktop'); await click('手动填写')
  await select(0, 'blocked'); await input(0, 'Preserved goal'); await input(1, 'Known failure'); await click('使用填写内容准备'); await click('取消交接')
  await act(async () => root.render(null)); await act(async () => root.render(React.createElement(HandoffAction, slot)))
  await click('交接给其他 Agent')
  await openHistory(); await flush()
  await click('查看交付')
  expect(document.body.querySelectorAll('textarea')[0].value).toBe('Preserved goal')
  expect(document.body.querySelectorAll('textarea')[0].disabled).toBe(true)
  await click('使用填写内容准备')
  expect(writes).toHaveLength(2)
  expect(writes[1]).toEqual(writes[0])
})
