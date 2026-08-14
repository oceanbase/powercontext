import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { checkGenerated, renderGeneratedSource } from '../scripts/gen-operations.mjs'

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

describe('generated operations check', () => {
  it('passes when the committed table matches OpenAPI', () => {
    expect(() => checkGenerated()).not.toThrow()
  })

  it('fails when the committed table has drifted', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pc-gen-'))
    const drifted = join(dir, 'operations.generated.ts')
    writeFileSync(drifted, 'export const OPERATIONS = {}\n')
    expect(() => checkGenerated(drifted)).toThrow(/drifted/)
  })

  it('renders the same source the repository currently commits', () => {
    const committed = readFileSync(join(repoRoot, 'src', 'operations.generated.ts'), 'utf8').replace(/\r\n/g, '\n')
    expect(renderGeneratedSource()).toBe(committed)
  })
})
