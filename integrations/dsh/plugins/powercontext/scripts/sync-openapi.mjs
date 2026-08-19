import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const dest = join(root, 'openapi', 'powercontext.yaml')

function existingFile(path) {
  return path && existsSync(path) ? resolve(path) : undefined
}

function walkForOpenApi(startDir) {
  let dir = resolve(startDir)
  for (let i = 0; i < 8; i += 1) {
    const candidate = join(dir, 'openapi', 'powercontext.yaml')
    if (existsSync(candidate)) return resolve(candidate)
    const parent = resolve(dir, '..')
    if (parent === dir) break
    dir = parent
  }
  return undefined
}

export function resolvePowerContextRoot() {
  const fromEnv = process.env.POWERCONTEXT_ROOT?.trim()
  if (fromEnv && existsSync(fromEnv)) return resolve(fromEnv)
  const yamlPath = walkForOpenApi(root)
  if (!yamlPath) return undefined
  return resolve(dirname(yamlPath), '..')
}

export function resolveOpenApiPath() {
  const fromEnv = existingFile(process.env.POWERCONTEXT_OPENAPI?.trim())
  if (fromEnv) return fromEnv
  const checkout = resolvePowerContextRoot()
  const fromRoot = checkout
    ? existingFile(join(checkout, 'openapi', 'powercontext.yaml'))
    : undefined
  if (fromRoot) return fromRoot
  const walked = walkForOpenApi(root)
  if (walked) return walked
  if (existsSync(dest)) return dest
  throw new Error(
    'openapi/powercontext.yaml is missing. Point POWERCONTEXT_ROOT or POWERCONTEXT_OPENAPI at a PowerContext checkout.',
  )
}

export function syncOpenApi() {
  const source = resolveOpenApiPath()
  if (source === dest) return dest
  mkdirSync(dirname(dest), { recursive: true })
  copyFileSync(source, dest)
  return dest
}

const invokedDirectly = process.argv[1] !== undefined
  && import.meta.url === pathToFileURL(process.argv[1]).href
if (invokedDirectly) {
  console.log(`openapi synced to ${syncOpenApi()}`)
}
