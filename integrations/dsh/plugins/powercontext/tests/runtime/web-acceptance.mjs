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

/**
 * Real-host browser acceptance for the PowerContext DSH sidebar status.
 *
 * This script drives an already installed DSH Web or Desktop Host through a
 * local Chrome DevTools endpoint. It never starts a fake DSH host and never
 * invents a response: the page, plugin route, Server, cookies, and keyboard
 * events are real. The caller owns the isolated DSH/Chrome processes.
 *
 * Required environment:
 *   DSH_WEB_URL   e.g. http://127.0.0.1:13080
 *   DSH_WEB_TOKEN launch token (or DSH_WEB_LOG containing it)
 *   DSH_CDP_URL   e.g. http://127.0.0.1:9223 (default)
 *
 * Usage:
 *   node tests/runtime/web-acceptance.mjs /tmp/dsh-web-acceptance.json
 */

import { readFileSync, writeFileSync } from 'node:fs'

const webUrl = (process.env.DSH_WEB_URL ?? 'http://127.0.0.1:3080').replace(/\/+$/, '')
const cdpUrl = (process.env.DSH_CDP_URL ?? 'http://127.0.0.1:9223').replace(/\/+$/, '')
const output = process.argv[2] ?? '/tmp/dsh-web-acceptance.json'
const logPath = process.env.DSH_WEB_LOG

function launchToken() {
  if (process.env.DSH_WEB_TOKEN) return process.env.DSH_WEB_TOKEN
  if (logPath === undefined) throw new Error('set DSH_WEB_TOKEN or DSH_WEB_LOG to the DSH launch log')
  const token = /token=([^\s]+)/.exec(readFileSync(logPath, 'utf8'))?.[1]
  if (token === undefined) throw new Error('the DSH launch log contains no launch token')
  return token
}

async function request(path, init = {}) {
  const response = await fetch(`${cdpUrl}${path}`, { ...init, signal: AbortSignal.timeout(5_000) })
  if (!response.ok) throw new Error(`CDP HTTP ${response.status} for ${path}`)
  return response.json()
}

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url)
    this.id = 0
    this.pending = new Map()
    this.events = []
    this.closed = false
  }

  async open() {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP websocket open timed out')), 5_000)
      this.ws.onopen = () => { clearTimeout(timer); resolve() }
      this.ws.onerror = (error) => { clearTimeout(timer); reject(error) }
    })
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id)
        if (pending === undefined) return
        clearTimeout(pending.timer)
        this.pending.delete(message.id)
        message.error
          ? pending.reject(new Error(`${pending.method}: ${message.error.message}`))
          : pending.resolve(message.result)
      } else {
        this.events.push(message)
      }
    }
    this.ws.onclose = () => {
      this.closed = true
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer)
        pending.reject(new Error(`${pending.method}: socket closed`))
      }
      this.pending.clear()
    }
  }

  send(method, params = {}, timeout = 15_000) {
    if (this.closed) return Promise.reject(new Error(`${method}: socket closed`))
    const id = ++this.id
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`${method}: CDP command timed out after ${timeout}ms`))
      }, timeout)
      this.pending.set(id, { resolve, reject, method, timer })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  close() {
    try { this.ws.close() } catch { /* already closed */ }
  }
}

async function evaluate(cdp, expression, timeout = 10_000) {
  const result = await cdp.send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  }, timeout)
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? 'evaluation failed')
  return result.result.value
}

async function waitFor(cdp, expression, timeout = 30_000, label = expression) {
  const deadline = Date.now() + timeout
  let last
  while (Date.now() < deadline) {
    try {
      last = await evaluate(cdp, expression)
      if (last) return last
    } catch (error) {
      last = String(error)
    }
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  throw new Error(`timeout waiting for ${label}; last=${JSON.stringify(last)?.slice(0, 400)}`)
}

async function dismissOnboarding(cdp) {
  const dismissed = []
  for (let attempt = 0; attempt < 6; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 500))
    const found = await evaluate(cdp, `(() => {
      const dialog = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
        .find(node => /开始使用|预览版说明/.test(node.innerText))
      if (!dialog) return null
      const button = [...dialog.querySelectorAll('button')]
        .find(node => (node.getAttribute('aria-label') ?? '') === '关闭') ?? dialog.querySelector('button')
      const label = button?.getAttribute('aria-label') ?? button?.innerText ?? 'clicked'
      button?.click()
      return label
    })()`)
    if (found === null) break
    dismissed.push(found)
  }
  return dismissed
}

async function clickByLabel(cdp, contains) {
  return evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('button')]
      .find(node => (node.getAttribute('aria-label') ?? '').includes(${JSON.stringify(contains)}))
    button?.click()
    return button !== undefined
  })()`)
}

const existingTarget = process.env.DSH_EXISTING_TARGET === '1'
const token = existingTarget ? '__no_launch_token_for_existing_desktop__' : launchToken()

async function desktopSettings(cdp, pattern) {
  const account = await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('button')]
      .find(node => /账号菜单|Account menu/.test(node.getAttribute('aria-label') ?? ''))
    button?.click()
    return button !== undefined
  })()`)
  if (!account) return false
  await new Promise(resolve => setTimeout(resolve, 300))
  const opened = await evaluate(cdp, `(() => {
    const item = [...document.querySelectorAll('[role="menu"] button, [role="menu"] [role="menuitem"]')]
      .find(node => /设置|Settings/.test(node.innerText))
    item?.click()
    return item !== undefined
  })()`)
  if (!opened) return false
  await new Promise(resolve => setTimeout(resolve, 500))
  const applied = await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('[role="dialog"] button')]
      .find(node => new RegExp(${JSON.stringify(pattern)}).test(node.innerText.trim()))
    button?.click()
    return button !== undefined
  })()`)
  await new Promise(resolve => setTimeout(resolve, 300))
  await evaluate(cdp, `(() => {
    const button = [...document.querySelectorAll('[role="dialog"] button')]
      .find(node => /^(关闭|Close)$/.test(node.innerText.trim()))
    button?.click()
  })()`)
  await new Promise(resolve => setTimeout(resolve, 500))
  return applied
}
const target = existingTarget
  ? (await request('/json/list')).find(candidate => candidate.type === 'page'
      && candidate.url.includes(process.env.DSH_TARGET_MATCH ?? 'dsh-app://app'))
  : await request('/json/new?about:blank', { method: 'PUT' })
if (target === undefined) throw new Error('no existing Desktop target matched DSH_TARGET_MATCH')
const cdp = new CDP(target.webSocketDebuggerUrl)
await cdp.open()
const mode = process.env.DSH_ACCEPTANCE_MODE ?? 'web'
const evidence = {
  mode,
  webUrl,
  target: target.id,
  checks: {},
  failures: [],
  errors: [],
}
let themeChanged = false
const failures = evidence.failures
function check(name, condition, detail) {
  if (!condition) failures.push({ name, detail })
}

try {
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')
  await cdp.send('Network.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  })
  if (!existingTarget) await cdp.send('Page.navigate', { url: `${webUrl}/?token=${token}` }, 20_000)
  await waitFor(cdp, 'document.readyState === "complete" && !!document.querySelector(".pc-trigger")', 45_000)
  await evaluate(cdp, `localStorage.removeItem('powercontext.dsh.locale')`)
  await cdp.send('Page.reload')
  await waitFor(cdp, 'document.readyState === "complete" && !!document.querySelector(".pc-trigger")', 45_000)
  evidence.checks.onboarding = await dismissOnboarding(cdp)
  await waitFor(cdp, 'document.querySelector(".pc-trigger").getAttribute("data-pc-status-trigger") === "ready"', 20_000, 'ready')
  evidence.checks.ready = await evaluate(cdp, `(() => {
    const trigger = document.querySelector('.pc-trigger')
    return {
      label: trigger.getAttribute('aria-label'),
      state: trigger.getAttribute('data-pc-status-trigger'),
      pluginEntries: (window.__DSH_BOOT__?.entries ?? []).map(entry => entry.id)
        .filter(id => id.includes('powercontext')),
    }
  })()`)
  check('plugin browser entry booted', evidence.checks.ready.pluginEntries.includes('powercontext-dsh'),
    JSON.stringify(evidence.checks.ready))
  check('sidebar reports verified connectivity', evidence.checks.ready.state === 'ready',
    JSON.stringify(evidence.checks.ready))

  if (await evaluate(cdp, `!!document.querySelector('.pc-panel')`)) {
    await evaluate(cdp, `document.querySelector('.pc-panel button[data-pc-close]')?.click()`)
    await waitFor(cdp, '!document.querySelector(".pc-panel")', 10_000, 'initial close')
  }
  await evaluate(cdp, `document.querySelector('.pc-trigger').click()`)
  await waitFor(cdp, '!!document.querySelector(".pc-panel")', 10_000, 'panel')
  await new Promise(resolve => setTimeout(resolve, 600))
  evidence.checks.panel = await evaluate(cdp, `(() => {
    const panel = document.querySelector('.pc-panel')
    const trigger = document.querySelector('.pc-trigger')
    const active = document.activeElement
    return {
      expanded: trigger.getAttribute('aria-expanded'),
      role: panel.getAttribute('role'),
      focusInside: panel.contains(active),
      focus: active.getAttribute?.('aria-label') ?? active.tagName,
      visibility: getComputedStyle(panel).visibility,
      text: panel.innerText.slice(0, 2_000),
      hasLaunchToken: panel.innerText.includes(${JSON.stringify(token)}),
      hasBearer: /Bearer\\s/i.test(panel.innerText),
    }
  })()`)
  evidence.checks.compact = await evaluate(cdp, `(() => {
    const panel = document.querySelector('.pc-panel')
    const details = panel.querySelector('details')
    return { language: document.querySelector('.pc-status').lang, text: panel.innerText,
      closed: details && !details.open, fits: panel.scrollWidth <= panel.clientWidth }
  })()`)
  check('Chinese compact panel is default', evidence.checks.compact.language === 'zh-CN'
    && evidence.checks.compact.closed && evidence.checks.compact.text.includes('技术详情')
    && !evidence.checks.compact.text.includes('就绪依赖') && evidence.checks.compact.fits,
    JSON.stringify(evidence.checks.compact))
  evidence.checks.versions = await evaluate(cdp, `(async () => {
    const payload = await (await fetch('/powercontext/status')).json()
    const footer = document.querySelector('.pc-panel-foot')
    const link = footer.querySelector('.pc-release-link')
    const control = footer.querySelector('.pc-update-check')
    const version = footer.querySelector('[data-pc-plugin-version]')
    const bounds = node => node.getBoundingClientRect()
    return { server: payload.server.package_version, plugin: payload.plugin_version,
      serverDetails: document.querySelector('[data-pc-server-version]')?.textContent,
      displayedPlugin: version.textContent,
      compact: !footer.querySelector('[data-pc-server-version]') && footer.scrollWidth <= footer.clientWidth
        && getComputedStyle(link).textDecorationLine === 'none'
        && bounds(link).right <= bounds(control).left && bounds(control).right <= bounds(version).left
        && bounds(link).top <= bounds(version).bottom && bounds(version).top <= bounds(link).bottom }
  })()`)
  check('footer displays one plugin version, with the Server version in details', !!evidence.checks.versions.server
    && evidence.checks.versions.server === evidence.checks.versions.serverDetails
    && evidence.checks.versions.plugin === evidence.checks.versions.displayedPlugin
    && evidence.checks.versions.compact, JSON.stringify(evidence.checks.versions))
  const updateMode = process.env.DSH_CHECK_UPDATES
  if (updateMode === '1' || updateMode === 'auto') {
    if (updateMode === '1') await clickByLabel(cdp, '检查更新')
    await waitFor(cdp, `document.querySelector('.pc-panel-foot')?.dataset.pcUpdateState === 'manual'`,
      40_000, 'release check completes')
    evidence.checks.updates = await evaluate(cdp, `(() => {
      const footer = document.querySelector('.pc-panel-foot')
      return { state: footer.dataset.pcUpdateState, release: footer.querySelector('.pc-release-link').href,
        detailLinks: [...document.querySelectorAll('.pc-details a')].map(link => link.href) }
    })()`)
    check('linked installation shows release notes and its manual update path', evidence.checks.updates.state === 'manual'
      && evidence.checks.updates.release.startsWith('https://github.com/oceanbase/powercontext/releases/tag/')
      && evidence.checks.updates.detailLinks.some(link => link.endsWith('/docs/integrations/dsh')),
    JSON.stringify(evidence.checks.updates))
  }
  await clickByLabel(cdp, '切换语言')
  await evaluate(cdp, `document.querySelector('.pc-language input[value="en"]').focus()`)
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: ' ', code: 'Space', windowsVirtualKeyCode: 32 })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: ' ', code: 'Space', windowsVirtualKeyCode: 32 })
  await waitFor(cdp, `document.querySelector('.pc-status').lang === 'en'`, 5_000, 'English language selection')
  check('English preference persists', await evaluate(cdp, `localStorage.getItem('powercontext.dsh.locale') === 'en'`), 'local preference')
  await cdp.send('Page.reload')
  await waitFor(cdp, `!!document.querySelector('.pc-trigger') && document.querySelector('.pc-status').lang === 'en'`, 45_000, 'persisted English')
  await evaluate(cdp, `document.querySelector('.pc-trigger').click()`)
  await waitFor(cdp, `!!document.querySelector('.pc-panel details')`, 10_000)
  await new Promise(resolve => setTimeout(resolve, 100))
  await evaluate(cdp, `document.querySelector('.pc-panel details summary').focus()`)
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
  await waitFor(cdp, `document.querySelector('.pc-panel details').open`, 5_000, 'keyboard details')
  const expandedText = await evaluate(cdp, `document.querySelector('.pc-panel').innerText`)
  check('expanded English details retain diagnostic fields', expandedText.includes('Readiness dependencies')
    && /automatic path/i.test(expandedText), expandedText)
  const expectedDeployment = mode === 'desktop' ? 'DSH Desktop' : 'Local Web'
  check('panel is a focused dialog', evidence.checks.panel.expanded === 'true'
    && evidence.checks.panel.role === 'dialog'
    && evidence.checks.panel.focusInside === true
    && evidence.checks.panel.visibility === 'visible', JSON.stringify(evidence.checks.panel))
  check('panel shows the verified deployment', expandedText.includes(expectedDeployment),
    `expected ${expectedDeployment}`)
  check('panel shows a live Scope', /当前 Scope\n[^\n]+/.test(evidence.checks.panel.text),
    evidence.checks.panel.text.slice(0, 300))
  check('panel does not expose launch token or Bearer value',
    evidence.checks.panel.hasLaunchToken === false && evidence.checks.panel.hasBearer === false,
    JSON.stringify({ token: evidence.checks.panel.hasLaunchToken, bearer: evidence.checks.panel.hasBearer }))
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await waitFor(cdp, '!document.querySelector(".pc-panel")', 10_000, 'Escape close')
  evidence.checks.escape = await evaluate(cdp, `({
    expanded: document.querySelector('.pc-trigger').getAttribute('aria-expanded'),
    focusRestored: document.activeElement === document.querySelector('.pc-trigger'),
  })`)
  check('Escape closes and restores trigger focus', evidence.checks.escape.expanded === 'false'
    && evidence.checks.escape.focusRestored === true, JSON.stringify(evidence.checks.escape))

  await cdp.send('Page.bringToFront')
  await evaluate(cdp, `document.querySelector('.pc-trigger').focus()`)
  await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 })
  await waitFor(cdp, '!!document.querySelector(".pc-panel")', 10_000, 'keyboard open')
  await new Promise(resolve => setTimeout(resolve, 300))
  evidence.checks.keyboard = await evaluate(cdp, `({
    open: !!document.querySelector('.pc-panel'),
    focusInside: document.querySelector('.pc-panel').contains(document.activeElement),
    focus: document.activeElement.getAttribute?.('aria-label') ?? document.activeElement.tagName,
  })`)
  check('Enter opens and focuses the dialog', evidence.checks.keyboard.open === true
    && evidence.checks.keyboard.focusInside === true, JSON.stringify(evidence.checks.keyboard))
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await waitFor(cdp, '!document.querySelector(".pc-panel")', 10_000, 'keyboard close')

  const toggle = () => evaluate(cdp, `document.querySelector('button.hHd-Xa_toggle')?.getAttribute('aria-label') ?? null`)
  if ((await toggle())?.includes('打开侧边栏')) await clickByLabel(cdp, '打开侧边栏')
  await new Promise(resolve => setTimeout(resolve, 300))
  await clickByLabel(cdp, '收起侧边栏')
  await new Promise(resolve => setTimeout(resolve, 600))
  evidence.checks.collapsed = await evaluate(cdp, `(() => {
    const trigger = document.querySelector('.pc-trigger')
    const rect = trigger.getBoundingClientRect()
    return {
      rail: trigger.classList.contains('pc-trigger-rail'),
      label: trigger.getAttribute('aria-label'),
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    }
  })()`)
  check('collapsed sidebar uses a 36px accessible rail trigger', evidence.checks.collapsed.rail === true
    && evidence.checks.collapsed.rect.width <= 40
    && String(evidence.checks.collapsed.label).startsWith('PowerContext status:'),
  JSON.stringify(evidence.checks.collapsed))
  await evaluate(cdp, `document.querySelector('.pc-trigger').click()`)
  await waitFor(cdp, '!!document.querySelector(".pc-panel")', 10_000, 'rail panel')
  await new Promise(resolve => setTimeout(resolve, 300))
  evidence.checks.railPanel = await evaluate(cdp, `(() => {
    const panel = document.querySelector('.pc-panel')
    const rect = panel.getBoundingClientRect()
    return {
      visibility: getComputedStyle(panel).visibility,
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      focusInside: panel.contains(document.activeElement),
    }
  })()`)
  check('diagnostics panel remains visible and focused from the rail',
    evidence.checks.railPanel.visibility === 'visible' && evidence.checks.railPanel.focusInside === true
    && evidence.checks.railPanel.rect.x >= 0, JSON.stringify(evidence.checks.railPanel))
  await evaluate(cdp, `document.querySelector('.pc-panel button[data-pc-close]')?.click()`)
  await clickByLabel(cdp, '打开侧边栏')
  await new Promise(resolve => setTimeout(resolve, 300))

  // DSH publishes dark mode through this body attribute (the theme presenter's
  // contract); setting it exercises the same token palette without rewriting
  // the user's persisted preference.
  themeChanged = true
  if (mode === 'desktop') {
    const themed = await desktopSettings(cdp, '^(深色|Dark)$')
    check('Desktop appearance can switch to dark', themed === true, 'settings menu/theme row not found')
  } else {
    await evaluate(cdp, `(() => {
      document.documentElement.dataset.dsThemeSource = 'dark'
      document.body.toggleAttribute('data-ds-dark-theme', true)
    })()`)
  }
  await new Promise(resolve => setTimeout(resolve, 400))
  await evaluate(cdp, `document.querySelector('.pc-trigger').click()`)
  await waitFor(cdp, '!!document.querySelector(".pc-panel")', 10_000, 'dark panel')
  await new Promise(resolve => setTimeout(resolve, 300))
  evidence.checks.dark = await evaluate(cdp, `(() => {
    const panel = document.querySelector('.pc-panel')
    const style = getComputedStyle(panel)
    return {
      darkAttribute: document.body.hasAttribute('data-ds-dark-theme'),
      bodyBg: getComputedStyle(document.body).backgroundColor,
      panelBg: style.backgroundColor,
      panelColor: style.color,
    }
  })()`)
  check('dark DSH palette is applied to the panel', evidence.checks.dark.darkAttribute === true
    && evidence.checks.dark.bodyBg !== 'rgb(255, 255, 255)'
    && evidence.checks.dark.panelBg !== 'rgba(252, 253, 254, 0.7)', JSON.stringify(evidence.checks.dark))
  await evaluate(cdp, `document.querySelector('.pc-panel button[data-pc-close]')?.click()`)
  if (mode === 'desktop') {
    const restored = await desktopSettings(cdp, '^(浅色|Light)$')
    check('Desktop appearance can restore light mode', restored === true, 'settings menu/theme row not found')
  } else {
    await evaluate(cdp, `(() => {
      document.documentElement.dataset.dsThemeSource = 'light'
      document.body.removeAttribute('data-ds-dark-theme')
    })()`)
  }
  themeChanged = false

  if (process.env.DSH_SESSION_FIXTURE === '1') {
    const cwdA = process.env.DSH_SESSION_CWD_A ?? '/tmp/dsh-session-a'
    const cwdB = process.env.DSH_SESSION_CWD_B ?? '/tmp/dsh-session-b'
    const create = async cwd => evaluate(cdp, `(async () => {
      const response = await fetch('/__powercontext_test/sessions', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ cwd: ${JSON.stringify(cwd)} }),
      })
      return { status: response.status, body: await response.json() }
    })()`)
    const firstCreated = await create(cwdA)
    const secondCreated = await create(cwdB)
    check('session fixture created both genuine sessions',
      firstCreated.status === 200 && secondCreated.status === 200
      && firstCreated.body.cwd === cwdA && secondCreated.body.cwd === cwdB,
      JSON.stringify({ firstCreated, secondCreated }))

    const readPanel = async () => {
      await waitFor(cdp, 'document.readyState === "complete" && !!document.querySelector(".pc-trigger")', 45_000)
      await dismissOnboarding(cdp)
      await waitFor(cdp, 'document.querySelector(".pc-trigger").getAttribute("data-pc-status-trigger") === "ready"', 20_000, 'session ready')
      await evaluate(cdp, `document.querySelector('.pc-trigger').click()`)
      await waitFor(cdp, '!!document.querySelector(".pc-panel")', 10_000, 'session panel')
      await new Promise(resolve => setTimeout(resolve, 500))
      await evaluate(cdp, `document.querySelector('.pc-panel details').open = true`)
      const text = await evaluate(cdp, `document.querySelector('.pc-panel').innerText`)
      await evaluate(cdp, `document.querySelector('.pc-panel button[data-pc-close]')?.click()`)
      return {
        text,
        id: /(session-[0-9a-zA-Z-]+)/.exec(text)?.[1] ?? null,
        scope: /Active Scope\n([^\n]+)/.exec(text)?.[1] ?? null,
      }
    }
    const originalCurrent = await evaluate(cdp, `localStorage.getItem('dsh.sessions.current')`)
    const select = async (id) => {
      await evaluate(cdp, `localStorage.setItem('dsh.sessions.current', JSON.stringify({ sessionId: ${JSON.stringify(id)} }))`)
      await new Promise(resolve => setTimeout(resolve, 500))
      await cdp.send('Page.reload', {}, 20_000)
      await new Promise(resolve => setTimeout(resolve, 1_500))
      return readPanel()
    }
    const first = await select(firstCreated.body.id)
    const second = await select(secondCreated.body.id)
    evidence.checks.sessionSwitch = {
      first: { created: firstCreated.body, observedId: first.id, observedScope: first.scope },
      second: { created: secondCreated.body, observedId: second.id, observedScope: second.scope },
      expectedScopes: [process.env.DSH_SESSION_SCOPE_A ?? null, process.env.DSH_SESSION_SCOPE_B ?? null],
    }
    check('first selected session is observed by the bridge', first.id === firstCreated.body.id,
      JSON.stringify(first))
    check('second selected session is distinct and observed by the bridge', second.id === secondCreated.body.id
      && second.id !== first.id, JSON.stringify(second))
    check('session switch resolves distinct Scopes', first.scope !== null && second.scope !== null
      && first.scope !== second.scope, JSON.stringify({ first: first.scope, second: second.scope }))
    if (process.env.DSH_SESSION_SCOPE_A) check('first Scope matches the bound fixture Scope',
      first.scope === process.env.DSH_SESSION_SCOPE_A, first.scope)
    if (process.env.DSH_SESSION_SCOPE_B) check('second Scope matches the bound fixture Scope',
      second.scope === process.env.DSH_SESSION_SCOPE_B, second.scope)
    const statusRequests = cdp.events
      .filter(event => event.method === 'Network.requestWillBeSent'
        && event.params.request.url.includes('/powercontext/status'))
      .map(event => event.params.request.url)
    check('status requests carry both exact session selectors',
      statusRequests.some(url => url.includes(`session_id=${firstCreated.body.id}`))
      && statusRequests.some(url => url.includes(`session_id=${secondCreated.body.id}`)),
      JSON.stringify(statusRequests))
    await evaluate(cdp, `localStorage.setItem('dsh.sessions.current', ${JSON.stringify(originalCurrent)})`)
      .catch(() => undefined)
  } else {
    evidence.checks.sessionSwitch = { skipped: 'set DSH_SESSION_FIXTURE=1 with the test-only session fixture' }
  }

  if (!existingTarget) {
    evidence.checks.unauthenticated = await evaluate(cdp, `(async () => {
      const response = await fetch('/powercontext/status', { cache: 'no-store', credentials: 'omit' })
      return { status: response.status, body: (await response.text()).slice(0, 200) }
    })()`)
    check('unauthenticated status is rejected', evidence.checks.unauthenticated.status === 401
      && evidence.checks.unauthenticated.body.includes('dsh_authentication_required'),
    JSON.stringify(evidence.checks.unauthenticated))
  } else {
    evidence.checks.unauthenticated = { skipped: 'Desktop shell owns its authenticated dsh-app origin' }
  }

  evidence.errors = cdp.events
    .filter(event => event.method === 'Runtime.exceptionThrown')
    .map(event => event.params.exceptionDetails?.exception?.description ?? event.params.exceptionDetails?.text)
    .slice(0, 10)
  check('page has no uncaught runtime exceptions', evidence.errors.length === 0, JSON.stringify(evidence.errors))
} catch (error) {
  evidence.failure = String(error)
} finally {
  if (themeChanged) {
    if (mode === 'desktop') await desktopSettings(cdp, '^(浅色|Light)$').catch(() => undefined)
    else {
      await evaluate(cdp, `(() => {
        document.documentElement.dataset.dsThemeSource = 'light'
        document.body.removeAttribute('data-ds-dark-theme')
      })()`, 5_000).catch(() => undefined)
    }
  }
  cdp.close()
  if (!existingTarget) {
    await fetch(`${cdpUrl}/json/close/${target.id}`, { signal: AbortSignal.timeout(5_000) })
      .catch(() => undefined)
  }
  writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`)
  process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`)
  if (evidence.failure !== undefined || failures.length > 0) process.exitCode = 1
}
