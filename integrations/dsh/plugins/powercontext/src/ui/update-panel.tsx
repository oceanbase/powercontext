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

import { useEffect, useId, useRef, useState } from 'react'
import {
  parseUpdateSnapshot, RELEASES_URL, UPDATE_ACTION_HEADER, UPDATE_PLAN_HEADER, UPDATE_ROUTE, type UpdateSnapshot,
} from '../update-contract.ts'
import { type Locale, texts } from './locale.ts'

const DOCS = 'https://powercontext.oceanbase.io'
const labels = {
  'zh-CN': {
    version: '版本：', check: '检查更新', update: '更新', updateTo: '更新插件至',
    notes: '发布说明', serverGuide: 'Server 升级指南', pluginGuide: '插件更新指南',
    idle: '', checking: '正在检查发布版本…', current: '插件已是最新版本。',
    available: '安装后可能需要重新加载或重启 DSH。',
    manual: '此安装方式需要手动更新，请保留原安装渠道。',
    server_first: '请先确认连接的 Server 已升级到匹配版本。',
    artifact_unavailable: '所用渠道尚无对应的插件包，请稍后再查。',
    incompatible: '此版本不适用于当前 DSH，请查看发布说明。',
    held: '已锁定插件更新，请检查插件配置。',
    check_failed: '检查未完成，请检查网络或重新登录 DSH 后重试。',
    plan_changed: '更新检查已过期或安装状态已变化，请重新检查。',
    installing: 'DSH 正在后台安装插件…',
    restart_required: '已安装，请重新加载或重启 DSH 以启用新版本。',
    updated: '已确认新插件版本生效。',
    restored: '安装未完成，已恢复原插件版本。',
    install_failed: '安装失败，请在 DSH 的插件设置中检查当前安装。',
    busy: 'DSH 仍有会话在运行，请在会话结束后重试。',
    unconfirmed: '安装结果未确认，请先在 DSH 的插件设置中检查版本。',
    serverMissing: '发布包尚不可用',
  },
  en: {
    version: 'Version: ', check: 'Check for updates', update: 'Update', updateTo: 'Update plugin to',
    notes: 'Release notes', serverGuide: 'Server upgrade guide', pluginGuide: 'Plugin update guide',
    idle: '', checking: 'Checking releases…', current: 'The plugin is up to date.',
    available: 'A DSH reload or restart may be needed after installation.',
    manual: 'Update manually using the original installation source.',
    server_first: 'First confirm the connected Server is on the matching version.',
    artifact_unavailable: 'The plugin package is unavailable on this channel. Check again later.',
    incompatible: 'This release cannot run on this DSH version. See the release notes.',
    held: 'Plugin updates are on hold. Check the plugin configuration.',
    check_failed: 'The check did not finish. Check the network or sign in to DSH again.',
    plan_changed: 'The check expired or the installation changed. Check again.',
    installing: 'DSH is installing the plugin in the background…',
    restart_required: 'Installed. Reload or restart DSH to use the new version.',
    updated: 'The new plugin version is active.',
    restored: 'Installation did not complete. The previous plugin was restored.',
    install_failed: 'Installation failed. Check the current installation in DSH Plugins.',
    busy: 'A DSH session is running. Try again when it finishes.',
    unconfirmed: 'The install result is unknown. Check the version in DSH Plugins before retrying.',
    serverMissing: 'Release package unavailable',
  },
} satisfies Record<Locale, Record<UpdateSnapshot['phase'], string> & Record<string, string>>

async function requestUpdate(signal: AbortSignal, action?: 'check' | 'install', plan?: string): Promise<{ snapshot: UpdateSnapshot | null; status?: number }> {
  try {
    const headers: Record<string, string> = { accept: 'application/json' }
    if (action) headers[UPDATE_ACTION_HEADER] = action
    if (plan) headers[UPDATE_PLAN_HEADER] = plan
    const response = await fetch(UPDATE_ROUTE, {
      method: action ? 'POST' : 'GET', headers, signal,
      credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    })
    return { status: response.status, snapshot: response.ok ? parseUpdateSnapshot(await response.json()) : null }
  } catch {
    return { snapshot: null }
  }
}

/** Read the Host snapshot only while the status panel is open. */
export function useVersionUpdates(open: boolean) {
  const [snapshot, setSnapshot] = useState<UpdateSnapshot | null>(null)
  const [sending, setSending] = useState(false)
  const [notice, setNotice] = useState<'check_failed' | 'plan_changed' | 'unconfirmed' | null>(null)
  const pending = useRef<AbortController | null>(null)
  const phase = snapshot?.phase ?? 'idle'
  const working = phase === 'checking' || phase === 'installing'

  useEffect(() => () => pending.current?.abort(), [])
  useEffect(() => {
    if (open) return
    pending.current?.abort()
    pending.current = null
    setSending(false)
  }, [open])
  useEffect(() => {
    if (!open || sending) return
    const lifetime = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let failures = 0
    const poll = async (): Promise<void> => {
      if (document.hidden) {
        timer = setTimeout(() => { void poll() }, 15_000)
        return
      }
      const { snapshot: state } = await requestUpdate(AbortSignal.any([lifetime.signal, AbortSignal.timeout(5_000)]))
      if (lifetime.signal.aborted) return
      if (state) {
        setSnapshot(state)
        if (state.phase !== 'idle') setNotice(null)
        failures = 0
      } else if (working && ++failures >= 8) {
        setNotice(phase === 'installing' ? 'unconfirmed' : 'check_failed')
        setSnapshot(null)
      }
      const fast = state?.phase === 'checking' || state?.phase === 'installing' || (!state && working && failures < 8)
      timer = setTimeout(() => { void poll() }, fast ? (failures > 0 ? 2_000 : 1_000) : 15_000)
    }
    void poll()
    return () => { lifetime.abort(); clearTimeout(timer) }
  }, [open, sending, working, phase])

  const submit = async (): Promise<void> => {
    if (!open || pending.current || working || phase === 'restart_required') return
    const plan = phase === 'available' && snapshot?.plan && snapshot.plugin_target ? snapshot.plan : undefined
    const lifetime = new AbortController()
    pending.current = lifetime
    setSending(true)
    setNotice(null)
    const result = await requestUpdate(AbortSignal.any([lifetime.signal, AbortSignal.timeout(6_000)]), plan ? 'install' : 'check', plan)
    if (lifetime.signal.aborted) return
    pending.current = null
    setSending(false)
    if (result.snapshot) setSnapshot(result.snapshot)
    else {
      setSnapshot(null)
      setNotice(result.status === 409 ? 'plan_changed' : plan ? 'unconfirmed' : 'check_failed')
    }
  }

  return { snapshot, phase, notice, busy: sending || working, submit }
}

type VersionUpdates = ReturnType<typeof useVersionUpdates>

export function VersionFooter({ locale, pluginVersion, updates }: {
  locale: Locale; pluginVersion?: string | null; updates: VersionUpdates
}) {
  const text = labels[locale]
  const description = useId()
  const { snapshot, phase, notice, busy, submit } = updates
  const target = phase === 'available' && snapshot?.plan ? snapshot.plugin_target : null
  const actionLabel = target ? text.updateTo + ' ' + target : text.check
  const message = !notice && phase === 'server_first' && snapshot?.plugin_target
    ? text.server_first + ' (' + snapshot.plugin_target + ')'
    : text[notice ?? phase]
  const version = snapshot?.plugin_version ?? pluginVersion ?? texts[locale].unknown
  const release = snapshot?.release_tag ? RELEASES_URL + '/tag/' + snapshot.release_tag : RELEASES_URL

  return <footer className="pc-panel-foot" data-pc-update-state={notice ?? phase}>
    <a className="pc-release-link" href={release} target="_blank" rel="noreferrer">{text.notes}</a>
    <div className="pc-version-actions">
      <button className={target ? 'pc-update-button' : 'pc-icon-button pc-update-check'} type="button"
        aria-label={actionLabel} aria-describedby={description} aria-busy={busy}
        title={message ? actionLabel + ' · ' + message : actionLabel}
        disabled={busy || phase === 'restart_required'} onClick={() => { void submit() }}>
        {target ? text.update : <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
          strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M13.5 3v4h-4M13.2 6.5a5.4 5.4 0 1 0-.4 4.4" />
        </svg>}
      </button>
      <span className="pc-running-version" title={text.version + version}>
        {text.version}<span className="pc-mono" data-pc-plugin-version>{version}</span>
      </span>
    </div>
    <span className="pc-sr-only" id={description} role="status">{message}</span>
  </footer>
}

export function UpdateDetails({ locale, serverVersion, updates }: {
  locale: Locale; serverVersion?: string | null; updates: VersionUpdates
}) {
  const text = labels[locale]
  const { snapshot, phase } = updates
  const docs = DOCS + (locale === 'zh-CN' ? '/zh' : '/en')
  const anchor = locale === 'zh-CN' ? '#更新或替换安装' : '#update-or-replace-an-installation'
  const serverTarget = snapshot?.server_target
  return <>
    <span className="pc-key">{texts[locale].serverVersion}</span>
    <span className="pc-value pc-mono" data-pc-server-version>{serverVersion ?? texts[locale].unknown}</span>
    <div className="pc-update-guide">
      {serverTarget && <span className="pc-mono">{serverTarget} · </span>}
      <a className="pc-release-link" href={docs + '/docs/get-started/install-and-run' + anchor}
        target="_blank" rel="noreferrer">{text.serverGuide}</a>
      {snapshot?.server_state === 'artifact_unavailable' && <span> · {text.serverMissing}</span>}
    </div>
    {phase === 'manual' && <div className="pc-update-guide">
      <a className="pc-release-link" href={docs + '/docs/integrations/dsh'}
        target="_blank" rel="noreferrer">{text.pluginGuide}</a>
    </div>}
  </>
}
