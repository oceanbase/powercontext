import { existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const built = join(root, 'lib', 'index.js')
const force = process.env.POWERCONTEXT_DSH_FORCE_BUILD === '1'

function run(file) {
  return spawnSync(process.execPath, [join(root, 'scripts', file)], {
    cwd: root,
    stdio: 'inherit',
  })
}

if (existsSync(built) && !force) {
  process.exit(0)
}

const gen = run('gen-operations.mjs')
if (gen.status !== 0) process.exit(gen.status ?? 1)

const tsdown = spawnSync('tsdown', { cwd: root, stdio: 'inherit', shell: true })
if (tsdown.status === 0) process.exit(0)
if (existsSync(built)) {
  console.warn('powercontext-dsh: tsdown unavailable; using prebuilt lib/')
  process.exit(0)
}
process.exit(tsdown.status ?? 1)
