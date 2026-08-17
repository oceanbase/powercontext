import { describe, expect, it } from 'vitest'
import { sanitizePublishManifest, stampPublishManifest } from '../scripts/stamp-version.mjs'

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
  })
})
