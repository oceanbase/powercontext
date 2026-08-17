import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { parse } from 'yaml'
import { parseOperations, renderOperationsSource } from './openapi-ops.mjs'
import { resolveOpenApiPath, syncOpenApi } from './sync-openapi.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const generatedPath = join(root, 'src', 'operations.generated.ts')
const DRIFT_MESSAGE = 'Generated API code drifted; run `pnpm gen` and review the result.'

export function renderGeneratedSource(yamlPath = resolveOpenApiPath()) {
  const doc = parse(readFileSync(yamlPath, 'utf8'))
  const rows = parseOperations(doc)
  if (rows.length === 0) {
    throw new Error('gen-operations: no operations parsed from openapi/powercontext.yaml')
  }
  return renderOperationsSource(rows)
}

function normalizeNewlines(text) {
  return text.replace(/\r\n/g, '\n')
}

export function checkGenerated(path = generatedPath) {
  const expected = renderGeneratedSource()
  const actual = readFileSync(path, 'utf8')
  if (normalizeNewlines(actual) !== normalizeNewlines(expected)) throw new Error(DRIFT_MESSAGE)
}

export function generateOperations() {
  const yamlPath = syncOpenApi()
  const source = renderGeneratedSource(yamlPath)
  mkdirSync(dirname(generatedPath), { recursive: true })
  writeFileSync(generatedPath, source)
  return generatedPath
}

function main() {
  if (process.argv.includes('--check')) {
    checkGenerated()
    console.log('generated operations are current')
    return
  }
  const path = generateOperations()
  console.log(`wrote operations to ${path}`)
}

const invokedDirectly = process.argv[1] !== undefined
  && import.meta.url === pathToFileURL(process.argv[1]).href
if (invokedDirectly) {
  main()
}
