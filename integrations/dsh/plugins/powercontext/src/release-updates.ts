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

import { compareVersions, releaseVersion } from './update-contract.ts'

const RELEASES = 'https://api.github.com/repos/oceanbase/powercontext/releases?per_page=30'
const MAX_METADATA_BYTES = 2 * 1024 * 1024

export interface OfficialRelease {
  tag: string
  version: NonNullable<ReturnType<typeof releaseVersion>>
  pluginPublished: boolean
  wheelDigest: string | null
}

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : {}
}

async function metadata(url: string, fetcher: typeof fetch): Promise<unknown> {
  const response = await fetcher(url, {
    redirect: 'error', signal: AbortSignal.timeout(6_000),
    headers: { accept: 'application/json', 'user-agent': 'powercontext-dsh-update-check' },
  })
  if (!response.ok || !response.body) {
    await response.body?.cancel()
    throw new Error('release metadata unavailable')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let json = ''
  let size = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return JSON.parse(json + decoder.decode())
      size += value.byteLength
      if (size > MAX_METADATA_BYTES) throw new Error('release metadata too large')
      json += decoder.decode(value, { stream: true })
    }
  } finally {
    await reader.cancel()
  }
}

export async function officialRelease(channel: 'stable' | 'preview', fetcher = fetch): Promise<OfficialRelease> {
  const releases = await metadata(RELEASES, fetcher)
  if (!Array.isArray(releases)) throw new Error('invalid release listing')
  const candidates = releases.flatMap(raw => {
    const release = object(raw)
    const tag = release.tag_name
    if (typeof tag !== 'string' || !tag.startsWith('powercontext-v') || release.draft !== false
      || typeof release.published_at !== 'string' || typeof release.prerelease !== 'boolean') return []
    const version = releaseVersion(tag.slice('powercontext-v'.length))
    if (!version || tag !== 'powercontext-v' + version.python
      || (channel === 'stable' && (version.prerelease || release.prerelease))) return []
    const assets = Array.isArray(release.assets) ? release.assets.map(object).filter(asset => asset.state === 'uploaded') : []
    const plugin = assets.find(asset => asset.name === 'powercontext-dsh-' + version.npm + '.tgz')
    const wheel = assets.find(asset => asset.name === 'powercontext-' + version.python + '-py3-none-any.whl')
    const digest = typeof wheel?.digest === 'string' && /^sha256:[a-f0-9]{64}$/.test(wheel.digest) ? wheel.digest.slice(7) : null
    return [{ tag, version, pluginPublished: plugin !== undefined, wheelDigest: digest }]
  }).sort((a, b) => compareVersions(b.version, a.version))
  if (!candidates[0]) throw new Error('no published PowerContext release found')
  return candidates[0]
}

export async function serverDistributionAvailable(release: OfficialRelease, fetcher = fetch): Promise<boolean> {
  if (release.wheelDigest === null) return false
  try {
    const value = object(await metadata('https://pypi.org/pypi/powercontext/' + release.version.python + '/json', fetcher))
    return Array.isArray(value.urls) && value.urls.map(object).some(file =>
      file.filename === 'powercontext-' + release.version.python + '-py3-none-any.whl'
      && file.yanked === false && object(file.digests).sha256 === release.wheelDigest)
  } catch {
    return false
  }
}
