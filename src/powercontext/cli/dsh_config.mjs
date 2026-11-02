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

// Read configuration using the installed DSH's parser and patch algorithm.
// Never prepare/load the real profile: native normalization runs only on disposable metadata.
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const plugin = 'powercontext-dsh'
class InspectionError extends Error {
  constructor(reason, location = '') {
    super(reason)
    this.location = location
  }
}
function fail(reason, location) { throw new InspectionError(reason, location) }
function read(location, action) {
  try { return action() } catch (error) {
    if (error instanceof InspectionError) throw error
    fail('Cannot read or compose DSH configuration', location)
  }
}
function isExpression(value) { return value && typeof value === 'object' && '__jsExpr' in value }

function installation(executable) {
  const real = realpathSync(executable)
  // npm/pnpm Windows shims live beside node_modules; Unix bins are symlinks
  // into the package. Resolve from the selected CLI, never the working directory.
  const candidates = [join(dirname(executable), 'node_modules', '@deepseek-ai', 'dsh', 'package.json')]
  // pnpm and user launchers may be scripts rather than symlinks. Read only a
  // literal quoted lib/bin.js path; never execute or source launcher contents.
  if (statSync(real).size < 65536) {
    const text = readFileSync(real, 'utf8')
    for (const match of text.matchAll(/["']([^"'\r\n]*[\\/]lib[\\/]bin\.js)["']/g)) {
      const path = match[1].replace(/^(?:%~dp0|%dp0%|\$basedir|\$\{basedir\})[\\/]?/, dirname(executable) + '/')
      if (path.includes('$') || path.includes('%') || !existsSync(path)) continue
      candidates.push(join(dirname(dirname(realpathSync(path))), 'package.json'))
    }
  }
  for (let dir = dirname(real); dirname(dir) !== dir; dir = dirname(dir)) {
    candidates.push(join(dir, 'package.json'))
  }
  for (const file of candidates) {
    if (!existsSync(file)) continue
    try {
      if (JSON.parse(readFileSync(file, 'utf8')).name === '@deepseek-ai/dsh') return file
    } catch { /* A parent manifest need not belong to DSH. */ }
  }
  fail('Cannot locate the installed DSH package from its CLI; use a supported npm/pnpm DSH installation')
}

async function inspect(executable, home, profile, prospective, desktopAnchor, requireInstalled) {
  const anchor = desktopAnchor || installation(executable)
  if (desktopAnchor) {
    const manifest = read(anchor, () => JSON.parse(readFileSync(anchor, 'utf8')))
    if (manifest.name !== '@deepseek-ai/dsh') fail('Invalid Desktop runtime manifest', anchor)
  }
  const require = createRequire(anchor)
  // These are the selected host's APIs, not dependencies of the plugin being installed.
  // Resolving a different copy from PowerContext would inspect a different host contract.
  let boot, bootPath
  try {
    bootPath = require.resolve('@deepseek-ai/dsh-app-boot')
    boot = await import(pathToFileURL(bootPath).href)
  }
  catch { fail('Installed DSH cannot provide its dsh-app-boot configuration APIs; upgrade or reinstall DSH', anchor) }
  for (const name of ['readProfileManifest', 'loadProfileDirectory', 'loadOverlayPatches', 'composeEntries']) {
    if (typeof boot[name] !== 'function') fail(`Installed DSH does not expose ${name}; upgrade or reinstall DSH`, anchor)
  }
  const dir = join(home, 'profiles', profile)
  const manifestPath = join(dir, 'package.json')
  const manifest = existsSync(manifestPath)
    ? read(manifestPath, () => boot.readProfileManifest('powercontext', dir))
    : undefined
  let template
  if (!manifest) {
    let templates = boot.PROFILE_TEMPLATES
    if (!templates?.[profile]) {
      try {
        let catalogPath
        for (const resolver of [require, createRequire(bootPath)]) {
          try { catalogPath = resolver.resolve('@deepseek-ai/dsh-official-bundles'); break }
          catch { /* The catalog belongs to either the CLI or its app-boot dependency. */ }
        }
        if (!catalogPath) fail('Installed DSH cannot provide its profile templates; upgrade or reinstall DSH', anchor)
        templates = (await import(pathToFileURL(catalogPath).href)).PROFILE_TEMPLATES
        if (!templates || typeof templates !== 'object' || Array.isArray(templates)) {
          fail('Installed DSH has no supported profile template catalog; upgrade or reinstall DSH', catalogPath)
        }
      } catch (error) {
        if (error instanceof InspectionError) throw error
        fail('Installed DSH cannot provide its profile templates; upgrade or reinstall DSH', anchor)
      }
    }
    template = templates?.[profile]
    if (!template) fail("Installed DSH has no default template for this profile; upgrade or reinstall DSH", anchor)
  }
  const bundles = manifest?.dsh?.profile?.bundles ?? (manifest ? [] : template?.bundles ?? template)
  if (!Array.isArray(bundles) || bundles.some(name => typeof name !== 'string')) {
    fail('Cannot determine the DSH profile bundle list', manifestPath)
  }
  if (requireInstalled && !bundles.includes(plugin)) fail('PowerContext is not enabled in this profile', manifestPath)
  // The native reader may normalize retired bundles by rewriting package.json.
  // Give it disposable metadata while resolving the real installed dependencies.
  // In the tested hosts, readProfileManifest/bundlePatchPaths only read bundle files;
  // only dropRetiredBundles writes, and it receives the snapshot profile directory.
  const snapshot = mkdtempSync(join(tmpdir(), 'powercontext-dsh-inspect-'))
  let composition
  try {
    writeFileSync(join(snapshot, 'package.json'), existsSync(manifestPath)
      ? read(manifestPath, () => readFileSync(manifestPath))
      : JSON.stringify({ private: true, dsh: { profile: { bundles } } }), { mode: 0o600 })
    const modules = join(dir, 'node_modules')
    if (existsSync(modules)) symlinkSync(resolve(modules), join(snapshot, 'node_modules'),
      process.platform === 'win32' ? 'junction' : 'dir')
    const compatibility = join(dir, 'compatibility.json')
    if (existsSync(compatibility)) writeFileSync(join(snapshot, 'compatibility.json'),
      read(compatibility, () => readFileSync(compatibility)), { mode: 0o600 })
    composition = read(dir, () => boot.loadProfileDirectory('powercontext', snapshot, anchor, { userLayer: false }))
  } finally {
    try { rmSync(snapshot, { recursive: true, force: true, maxRetries: 3 }) }
    catch { /* Temporary-file cleanup must not replace the inspection result. */ }
  }
  if (!Array.isArray(composition?.layers) || composition.layers.some(layer => !Array.isArray(layer?.patches))) {
    fail('Installed DSH does not expose the expected profile composition; upgrade or reinstall DSH', anchor)
  }
  const skipped = composition.skippedBundles ?? []
  if (!Array.isArray(skipped) || skipped.some(entry => !entry || typeof entry.packageName !== 'string'
    || typeof entry.reason !== 'string')) {
    fail('Installed DSH does not expose the expected profile composition; upgrade or reinstall DSH', anchor)
  }
  const skippedBundles = skipped.map(({ packageName, reason }) => {
    const incompatible = /incompatible with (?:@deepseek-ai\/)?dsh\b/.test(reason)
    if (packageName === plugin) fail(incompatible
      ? 'PowerContext is incompatible with the installed DSH; resolve its version compatibility'
      : 'PowerContext cannot be loaded by the installed DSH; check host compatibility and reinstall its bundle', manifestPath)
    // Native reasons can contain raw YAML and credentials. Report only the omitted identity.
    return `DSH skipped ${incompatible ? 'an incompatible' : 'a'} bundle; its patches were not applied: ${packageName}`
  })
  const layers = composition.layers.map(layer => layer.patches)
  // Expose the future plugin id to user patches before native installation.
  if (!bundles.includes(plugin) && prospective) layers.push([{ insert: [{ id: plugin, name: plugin, config: {} }] }])
  const userFiles = [join(dir, 'cordis.patch.yml'), join(home, 'cordis.patch.yml')]
  for (const file of userFiles) {
    if (existsSync(file)) layers.push(read(file, () => boot.loadOverlayPatches('powercontext', file)))
  }
  const warnings = []
  const rows = read(dir, () => boot.composeEntries(layers, warning => warnings.push(warning)))
  if (warnings.some(warning => warning.includes(plugin))) fail('A PowerContext patch could not be applied', dir)
  const matches = []
  let includeApi, includeYaml
  const includeStack = new Set()
  async function includeEntries(config, baseUrl, location) {
    if (!config || typeof config !== 'object' || Array.isArray(config) || '__jsExpr' in config
      || typeof config.path !== 'string' || !config.path) {
      fail('Native DSH include path must be static before setup', location)
    }
    const file = read(location, () => fileURLToPath(new URL(config.path, baseUrl)))
    if (!['.yaml', '.yml', '.json'].includes(extname(file))) {
      fail('Native DSH includes must use readable YAML or JSON files before setup', location)
    }
    if (!existsSync(file)) {
      // Do not let Include.initial create a tree after preflight accepted it.
      fail('Native DSH include file must exist before setup; materialize it first', file)
    }
    const canonical = read(file, () => realpathSync(file))
    if (includeStack.has(canonical)) fail('Native DSH include tree contains a cycle', file)
    if (config.patches != null && !Array.isArray(config.patches)) {
      fail('Native DSH include patches must be a static list before setup', file)
    }
    // Include is an EntryGroup: the native Loader passes its config through
    // without interpolating !!js. Require static patch structure while keeping
    // ordinary plugin config expressions for the owning plugin to evaluate.
    for (const patch of config.patches ?? []) {
      if (!patch || typeof patch !== 'object' || Array.isArray(patch) || isExpression(patch)
        || [patch.id, patch.name, patch.group].some(isExpression)
        || (patch.insert != null && !Array.isArray(patch.insert))) {
        fail('Native DSH include patch structure must be static before setup', file)
      }
    }
    if (!includeApi) {
      try {
        const nativeRequire = createRequire(bootPath)
        const includePath = nativeRequire.resolve('@deepseek-ai/cordis-plugin-include')
        includeApi = await import(pathToFileURL(includePath).href)
        includeYaml = createRequire(includePath)('js-yaml')
      } catch {
        fail('Installed DSH cannot provide its native include inspection APIs; upgrade or reinstall DSH', location)
      }
      if (typeof includeApi.applyEntryPatches !== 'function' || !includeApi.entryListSchema) {
        fail('Installed DSH cannot provide its native include inspection APIs; upgrade or reinstall DSH', location)
      }
    }
    const entries = read(file, () => {
      const text = readFileSync(file, 'utf8')
      const data = extname(file) === '.json' ? JSON.parse(text)
        : includeYaml.load(text, { schema: includeApi.entryListSchema })
      if (!Array.isArray(data)) fail('Native DSH include file must contain an entry list', file)
      // Include patches target this tree, not the outer profile's entries.
      return includeApi.applyEntryPatches(data, config.patches, () => {})
    })
    return { entries, file, canonical, baseUrl: new URL('.', pathToFileURL(file)).href }
  }
  async function visit(entries, disabled = false, conditional = false,
    baseUrl = pathToFileURL(dir + '/').href, location = dir) {
    for (const row of entries) {
      if (!row || typeof row !== 'object' || Array.isArray(row) || '__jsExpr' in row) {
        fail('DSH plugin entries must be static mappings before setup', location)
      }
      if ([row.id, row.name, row.group].some(isExpression)) {
        fail('DSH plugin identity must be static before setup', location)
      }
      const dynamicDisabled = isExpression(row.disabled)
      const conditionalEntry = conditional || dynamicDisabled
      const unavailable = disabled || (!dynamicDisabled && Boolean(row.disabled))
      if (row.id === plugin || row.name === plugin) {
        if (row.id !== plugin || row.name !== plugin || unavailable || conditionalEntry) {
          fail('PowerContext is renamed, disabled, or conditionally enabled in DSH', location)
        }
        matches.push(row)
      }
      if (row.name === '@deepseek-ai/cordis-plugin-include' || row.name === 'cordis:include') {
        if (unavailable && !conditionalEntry) continue
        const tree = await includeEntries(row.config, baseUrl, location)
        includeStack.add(tree.canonical)
        try { await visit(tree.entries, unavailable, conditionalEntry, tree.baseUrl, tree.file) }
        finally { includeStack.delete(tree.canonical) }
      } else if (row.group || row.name === '@deepseek-ai/cordis-plugin-group' || row.name === 'cordis:group') {
        if (!Array.isArray(row.config)) fail('DSH group children must be a static entry list before setup', location)
        await visit(row.config, unavailable, conditionalEntry, baseUrl, location)
      }
    }
  }
  await visit(rows)
  if (matches.length > 1) fail('Multiple PowerContext entries make the DSH transport ambiguous', dir)
  if (!matches.length && (requireInstalled || prospective || bundles.includes(plugin))) {
    fail('The composed DSH profile does not contain PowerContext', dir)
  }
  const config = matches[0]?.config ?? {}
  if (!config || typeof config !== 'object' || Array.isArray(config) || '__jsExpr' in config) {
    fail('PowerContext config is not a static object', dir)
  }
  const settings = {}
  for (const [key, type] of [['baseUrl', 'string'], ['allowInsecureHttp', 'boolean']]) {
    if (config[key] === undefined) continue
    if (typeof config[key] !== type || (type === 'string' && !config[key].trim())) {
      fail(`PowerContext ${key} must be a static ${type}; dynamic transport expressions cannot be checked`, dir)
    }
    settings[key] = config[key]
  }
  return { settings, warnings: skippedBundles }
}

try {
  const [executable, home, profile, prospective, desktopAnchor, requireInstalled] = process.argv.slice(2)
  const result = await inspect(resolve(executable), resolve(home), profile, prospective === 'true', desktopAnchor, requireInstalled === 'true')
  process.stdout.write(JSON.stringify(result))
} catch (error) {
  // Native parser messages and stacks can include credentials or whole YAML rows.
  process.stdout.write(JSON.stringify({ error: error instanceof InspectionError
    ? { reason: error.message, location: error.location }
    : { reason: 'Cannot inspect the installed DSH configuration APIs', location: '' } }))
  process.exitCode = 1
}
