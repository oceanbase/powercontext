import { readFileSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stampPublishManifest } from './stamp-version.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const manifestPath = join(root, 'package.json')
const original = readFileSync(manifestPath, 'utf8')
const current = JSON.parse(original)
const version = process.argv[2]?.trim() || current.version

try {
  const packed = stampPublishManifest(current, version)
  writeFileSync(manifestPath, `${JSON.stringify(packed, null, 2)}\n`)
  const result = spawnSync('pnpm', ['pack'], { cwd: root, stdio: 'inherit', shell: true })
  if (result.status !== 0) process.exit(result.status ?? 1)
} finally {
  writeFileSync(manifestPath, original)
}
