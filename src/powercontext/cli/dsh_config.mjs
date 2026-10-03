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
// Never call loadProfile/prepareProfile: those initialize or rewrite host files.
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

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

async function inspect(executable, home, profile, candidate, prospective, desktopAnchor, requireInstalled) {
  const anchor = desktopAnchor || installation(executable)
  if (desktopAnchor) {
    const manifest = read(anchor, () => JSON.parse(readFileSync(anchor, 'utf8')))
    if (manifest.name !== '@deepseek-ai/dsh') fail('Invalid Desktop runtime manifest', anchor)
  }
  const require = createRequire(anchor)
  // These are the selected host's APIs, not dependencies of the plugin being installed.
  // Resolving a different copy from PowerContext would inspect a different host contract.
  let boot
  try { boot = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-app-boot')).href) }
  catch { fail('Installed DSH cannot provide its dsh-app-boot configuration APIs; upgrade or reinstall DSH', anchor) }
  for (const name of ['readProfileManifest', 'resolveBundleDir', 'loadOverlayPatches', 'composeEntries']) {
    if (typeof boot[name] !== 'function') fail(`Installed DSH does not expose ${name}; upgrade or reinstall DSH`, anchor)
  }
  const dir = join(home, 'profiles', profile)
  const manifestPath = join(dir, 'package.json')
  const manifest = existsSync(manifestPath)
    ? read(manifestPath, () => boot.readProfileManifest('powercontext', dir))
    : undefined
  const template = boot.PROFILE_TEMPLATES?.[profile]
  let bundles = manifest?.dsh?.profile?.bundles ?? (manifest ? [] : template?.bundles ?? template)
  if (!Array.isArray(bundles) || bundles.some(name => typeof name !== 'string')) {
    fail('Cannot determine the DSH profile bundle list', manifestPath)
  }
  if (requireInstalled && !bundles.includes(plugin)) fail('PowerContext is not enabled in this profile', manifestPath)
  let candidateBundle = candidate
  if (candidate) {
    // Both native reconciliation and composition prefer installation-owned
    // packages. The native resolver also handles packages hiding package.json.
    try { candidateBundle = boot.resolveBundleDir('powercontext', plugin, anchor, dirname(anchor)) }
    catch { /* The candidate supplies the profile-owned package. */ }
  }
  // With a materialized candidate, project the same direct-dependency
  // reconciliation performed by the selected CLI. Doctor reads current bundles.
  if (candidate) {
    const dependencies = manifest?.dependencies ?? {}
    if (!dependencies || typeof dependencies !== 'object' || Array.isArray(dependencies)) {
      fail('Cannot determine the DSH profile dependencies', manifestPath)
    }
    const before = new Set(Object.keys(dependencies))
    const after = [...new Set([...before, plugin])]
    // pnpm sorts dependency keys when saving a changed manifest. A no-op add
    // leaves its original bytes and ordering intact.
    const spec = 'link:' + resolve(candidate).replaceAll('\\', '/')
    if (dependencies[plugin] !== spec) after.sort()
    const cli = read(anchor, () => JSON.parse(readFileSync(anchor, 'utf8')))
    const preservesInactive = !!cli.dependencies?.['@deepseek-ai/dsh-plugin-manager']
    const declarations = new Map()
    function declaresBundle(name) {
      if (declarations.has(name)) return declarations.get(name)
      let packageDir
      if (name === plugin) packageDir = candidateBundle
      else {
        try { packageDir = boot.resolveBundleDir('powercontext', name, anchor, dir) }
        catch { declarations.set(name, false); return false }
      }
      const file = join(packageDir, 'package.json')
      const metadata = read(file, () => boot.readProfileManifest('powercontext', packageDir))
      const declared = metadata.dsh?.bundle?.patch !== undefined
      declarations.set(name, declared)
      return declared
    }
    const managed = new Set(after)
    bundles = bundles.filter(name => !managed.has(name) || declaresBundle(name))
    for (const name of after) {
      // Newer CLI operations preserve an already-installed inactive dependency;
      // legacy CLI reconciliation enables every dependency declaring a patch.
      if (preservesInactive && before.has(name)) continue
      if (!bundles.includes(name) && declaresBundle(name)) bundles.push(name)
    }
    if (!bundles.includes(plugin)) {
      fail('DSH installation would leave PowerContext disabled; enable its bundle in this profile before setup', manifestPath)
    }
  }
  const exemptions = typeof boot.readProfileVersionExemptions === 'function'
    ? read(dir, () => boot.readProfileVersionExemptions(dir)) : {}
  const skippedBundles = []
  function bundle(name, packageDir) {
    const file = join(packageDir, 'package.json')
    const metadata = read(file, () => boot.readProfileManifest('powercontext', packageDir))
    if (typeof boot.evaluatePluginCompatibility === 'function') {
      const issue = read(file, () => boot.evaluatePluginCompatibility(metadata, exemptions))
      if (issue && !issue.exempted) {
        if (name === plugin) fail('PowerContext is incompatible with the installed DSH; resolve its version compatibility', file)
        // Native DSH omits incompatible bundle layers. Composing their patches after
        // merely warning would validate transport settings the host will never use.
        skippedBundles.push(`DSH skipped an incompatible third-party bundle; its patches were not applied: ${file}`)
        return []
      }
    }
    let files
    if (typeof boot.bundlePatchPaths === 'function') {
      files = read(file, () => boot.bundlePatchPaths(packageDir, metadata.dsh?.bundle))
    } else {
      // Older DSH releases accept one patch path, not the newer list form.
      const patch = metadata.dsh?.bundle?.patch
      if (typeof patch !== 'string' || !patch) fail('DSH bundle has no supported patch declaration', file)
      files = [join(packageDir, patch)]
    }
    return files.flatMap(path => read(path, () => boot.loadOverlayPatches('powercontext', path)))
  }
  const layers = bundles.map(name => {
    if (name === plugin && candidate) return bundle(name, candidateBundle)
    const packageDir = read(manifestPath, () => boot.resolveBundleDir('powercontext', name, anchor, dir))
    return bundle(name, packageDir)
  })
  if (!bundles.includes(plugin)) {
    if (candidate) layers.push(bundle(plugin, candidate))
    // Before materializing the source, expose the future plugin id to user
    // patches. The installer rechecks with the actual candidate before add.
    else if (prospective) layers.push([{ insert: [{ id: plugin, name: plugin, config: {} }] }])
  }
  const userFiles = [join(dir, 'cordis.patch.yml'), join(home, 'cordis.patch.yml')]
  for (const file of userFiles) {
    if (existsSync(file)) layers.push(read(file, () => boot.loadOverlayPatches('powercontext', file)))
  }
  const warnings = []
  const rows = read(dir, () => boot.composeEntries(layers, warning => warnings.push(warning)))
  if (warnings.some(warning => warning.includes(plugin))) fail('A PowerContext patch could not be applied', dir)
  const matches = []
  function visit(entries, disabled = false) {
    for (const row of entries) {
      const unavailable = disabled || (row.disabled != null && row.disabled !== false)
      if (row.id === plugin || row.name === plugin) {
        if (row.id !== plugin || row.name !== plugin || unavailable) {
          fail('PowerContext is renamed, disabled, or conditionally enabled in DSH', dir)
        }
        matches.push(row)
      }
      if (row.group && Array.isArray(row.config)) visit(row.config, unavailable)
    }
  }
  visit(rows)
  if (matches.length > 1) fail('Multiple PowerContext entries make the DSH transport ambiguous', dir)
  if (!matches.length && (requireInstalled || candidate || prospective || bundles.includes(plugin))) {
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
  const [executable, home, profile, candidate, prospective, desktopAnchor, requireInstalled] = process.argv.slice(2)
  const result = await inspect(resolve(executable), resolve(home), profile, candidate || undefined, prospective === 'true', desktopAnchor, requireInstalled === 'true')
  process.stdout.write(JSON.stringify(result))
} catch (error) {
  // Native parser messages and stacks can include credentials or whole YAML rows.
  process.stdout.write(JSON.stringify({ error: error instanceof InspectionError
    ? { reason: error.message, location: error.location }
    : { reason: 'Cannot inspect the installed DSH configuration APIs', location: '' } }))
  process.exitCode = 1
}
