import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { resolveOpenApiPath, resolvePowerContextRoot } from '../scripts/sync-openapi.mjs'

const originalEnv = {
  POWERCONTEXT_OPENAPI: process.env.POWERCONTEXT_OPENAPI,
  POWERCONTEXT_ROOT: process.env.POWERCONTEXT_ROOT,
}

afterEach(() => {
  if (originalEnv.POWERCONTEXT_OPENAPI === undefined) delete process.env.POWERCONTEXT_OPENAPI
  else process.env.POWERCONTEXT_OPENAPI = originalEnv.POWERCONTEXT_OPENAPI
  if (originalEnv.POWERCONTEXT_ROOT === undefined) delete process.env.POWERCONTEXT_ROOT
  else process.env.POWERCONTEXT_ROOT = originalEnv.POWERCONTEXT_ROOT
})

describe('resolveOpenApiPath', () => {
  it('prefers POWERCONTEXT_OPENAPI when the file exists', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pc-openapi-'))
    const yamlPath = join(dir, 'powercontext.yaml')
    writeFileSync(yamlPath, 'openapi: 3.1.0\n')
    process.env.POWERCONTEXT_OPENAPI = yamlPath
    expect(resolveOpenApiPath()).toBe(yamlPath)
  })

  it('uses POWERCONTEXT_ROOT/openapi/powercontext.yaml next', () => {
    const root = mkdtempSync(join(tmpdir(), 'pc-root-'))
    mkdirSync(join(root, 'openapi'))
    const yamlPath = join(root, 'openapi', 'powercontext.yaml')
    writeFileSync(yamlPath, 'openapi: 3.1.0\n')
    delete process.env.POWERCONTEXT_OPENAPI
    process.env.POWERCONTEXT_ROOT = root
    expect(resolveOpenApiPath()).toBe(yamlPath)
    expect(resolvePowerContextRoot()).toBe(root)
  })
})
