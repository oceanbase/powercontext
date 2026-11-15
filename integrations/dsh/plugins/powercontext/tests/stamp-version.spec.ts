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

import { describe, expect, it } from 'vitest'
import { releaseMetadata, sanitizePublishManifest, stampPublishManifest } from '../scripts/stamp-version.mjs'

const sourceManifest = {
  name: 'powercontext-dsh',
  version: '0.0.1',
  files: ['lib/index.js', 'cordis.patch.yml'],
  dsh: { bundle: { patch: './cordis.patch.yml' } },
  scripts: {
    prepare: 'node scripts/prepare.mjs',
    build: 'pnpm gen && tsdown',
    test: 'vitest run',
  },
  peerDependencies: { '@deepseek-ai/cordis': '*' },
  devDependencies: {
    '@types/node': '^24.3.0',
    vitest: '^3.2.4',
  },
}

describe('sanitizePublishManifest', () => {
  it('drops scripts and devDependencies so Windows pnpm does not symlink them', () => {
    const packed = sanitizePublishManifest(sourceManifest)
    expect(packed.scripts).toBeUndefined()
    expect(packed.devDependencies).toBeUndefined()
    expect(packed.dsh).toEqual(sourceManifest.dsh)
    expect(packed.files).toEqual(sourceManifest.files)
    expect(packed.peerDependencies).toEqual(sourceManifest.peerDependencies)
  })

  it('does not mutate the source manifest', () => {
    sanitizePublishManifest(sourceManifest)
    expect(sourceManifest.scripts?.prepare).toBe('node scripts/prepare.mjs')
    expect(sourceManifest.devDependencies?.['@types/node']).toBe('^24.3.0')
  })
})

describe('stampPublishManifest', () => {
  it('stamps the version onto a sanitized publish manifest', () => {
    const packed = stampPublishManifest(sourceManifest, '0.0.2')
    expect(packed.version).toBe('0.0.2')
    expect(packed.scripts).toBeUndefined()
    expect(packed.devDependencies).toBeUndefined()
    expect(packed.name).toBe('powercontext-dsh')
  })

  it('rejects a missing or invalid version', () => {
    expect(() => stampPublishManifest(sourceManifest, '')).toThrow(/semver/)
    expect(() => stampPublishManifest(sourceManifest, 'v0.0.2')).toThrow(/semver/)
    for (const version of ['01.2.3', '1.2.3-01', '1.2.3-', '1.2.3-a..b', '1.2.3+']) {
      expect(() => stampPublishManifest(sourceManifest, version)).toThrow(/semver/)
    }
  })
})

describe('releaseMetadata', () => {
  it.each([
    ['powercontext-v1.5.0', '1.5.0', 'latest'],
    ['v1.6.0a1', '1.6.0-alpha.1', 'next'],
    ['1.6.0b2', '1.6.0-beta.2', 'next'],
    ['powercontext-v1.6.0rc3', '1.6.0-rc.3', 'next'],
  ])('maps Release %s to npm version and channel', (input, version, tag) => {
    expect(releaseMetadata(input)).toEqual({ version, tag })
  })

  it('keeps a GitHub prerelease off latest even with a stable version', () => {
    expect(releaseMetadata('v1.6.0', true)).toEqual({ version: '1.6.0', tag: 'next' })
  })

  it.each(['master', 'v01.2.3', 'v1.2.3rc01', 'v1.2.3; echo unsafe'])('rejects %s', tag => {
    expect(() => releaseMetadata(tag)).toThrow(/Release tag/)
  })
})
