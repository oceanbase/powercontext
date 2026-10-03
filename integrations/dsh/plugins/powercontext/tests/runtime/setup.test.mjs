/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import { installIntoHome, setupFixture } from './setup-fixture.mjs'

test('setup and repeated setup preserve an existing customized DSH profile', { timeout: 240000 }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'pc-dsh-setup-'))
  try {
    const installation = await installIntoHome(home, { customized: true })
    assert.match(installation.setup, /setup complete/)
    assert.equal(installation.doctor.ok, true)
    assert.equal(installation.doctor.checks.plugin.checks.registration, 'present')
  } finally {
    assert.equal(dirname(resolve(home)), resolve(tmpdir()))
    rmSync(home, { recursive: true, force: true })
  }
})

async function inactiveTransportBundle(home, config, { installed = true } = {}) {
  const fixture = setupFixture(home)
  const bundle = join(home, 'transport-override')
  mkdirSync(bundle)
  writeFileSync(join(bundle, 'package.json'), JSON.stringify({
    name: 'transport-override', version: '1.0.0', dsh: { bundle: { patch: './cordis.patch.yml' } },
  }))
  writeFileSync(join(bundle, 'cordis.patch.yml'), JSON.stringify([{ id: 'powercontext-dsh', config }]))
  if (installed) {
    await fixture.native(['plugin', '--profile', 'web', 'add',
      join(fixture.root, 'integrations/dsh/plugins/powercontext')])
  }
  await fixture.native(['plugin', '--profile', 'web', 'add', bundle])
  const manifest = join(fixture.profile, 'package.json')
  const metadata = JSON.parse(readFileSync(manifest, 'utf8'))
  metadata.dsh.profile.bundles = metadata.dsh.profile.bundles.filter(name => name !== 'transport-override')
  writeFileSync(manifest, JSON.stringify(metadata, undefined, 2) + '\n')
  writeFileSync(fixture.patch, '- id: powercontext-dsh\n  disabled: false\n')
  writeFileSync(fixture.homePatch, '- id: agent-default-model\n  name: "@deepseek-ai/dsh-agent-default-model"\n')
  writeFileSync(fixture.clients, JSON.stringify({ version: 1, hosts: {
    dsh: { server_url: 'https://saved.example', allow_insecure_http: false },
    pi: { server_url: 'https://unrelated.example', allow_insecure_http: false },
  } }, undefined, 2) + '\n')
  return { ...fixture, manifest }
}

for (const scenario of [
  { name: 'endpoint override during reinstall', installed: true,
    config: { baseUrl: 'https://unexpected.example' }, endpoint: 'https://selected.example',
    consent: '--no-allow-insecure-http', error: /baseUrl conflicts/ },
  { name: 'endpoint override during first installation', installed: false,
    config: { baseUrl: 'https://unexpected.example' }, endpoint: 'https://selected.example',
    consent: '--no-allow-insecure-http', error: /baseUrl conflicts/ },
  { name: 'endpoint-bound HTTP refusal', installed: true,
    config: { baseUrl: 'http://selected.example', allowInsecureHttp: false }, endpoint: 'http://selected.example',
    consent: '--allow-insecure-http', error: /HTTP consent/ },
]) {
  test(`setup rejects an inactive bundle's ${scenario.name} without changing saved state`, { timeout: 240000 }, async () => {
    const home = mkdtempSync(join(tmpdir(), 'pc-dsh-setup-conflict-'))
    try {
      const fixture = await inactiveTransportBundle(home, scenario.config, { installed: scenario.installed })
      const paths = [fixture.manifest, fixture.patch, fixture.homePatch, fixture.clients]
      const before = paths.map(path => readFileSync(path))
      await assert.rejects(fixture.cli(['setup', 'dsh', '--source', fixture.root,
        '--server-url', scenario.endpoint, scenario.consent, '--json']), error => {
        assert.equal(error.code, 1)
        assert.match(error.stderr, scenario.error)
        return true
      })
      for (const [index, path] of paths.entries()) assert.deepEqual(readFileSync(path), before[index])
    } finally {
      assert.equal(dirname(resolve(home)), resolve(tmpdir()))
      rmSync(home, { recursive: true, force: true })
    }
  })
}

test('matching inactive bundle settings survive setup and repeated setup in native configuration', { timeout: 240000 }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'pc-dsh-setup-matching-'))
  try {
    const fixture = await inactiveTransportBundle(home, {
      baseUrl: 'http://selected.example', allowInsecureHttp: true,
    })
    const patches = [fixture.patch, fixture.homePatch]
    const original = patches.map(path => readFileSync(path))
    let manifest
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const result = JSON.parse(await fixture.cli(['setup', 'dsh', '--source', fixture.root,
        '--server-url', 'http://selected.example', '--allow-insecure-http', '--json']))
      assert.equal(result.plugin, 'powercontext-dsh')
      const settings = JSON.parse(readFileSync(fixture.clients, 'utf8')).hosts
      assert.deepEqual(settings.dsh, { server_url: 'http://selected.example', allow_insecure_http: true })
      assert.deepEqual(settings.pi, { server_url: 'https://unrelated.example', allow_insecure_http: false })
      const dump = await fixture.native(['--profile', 'web', '--dump-config'])
      const entry = dump.match(/- id: powercontext-dsh\r?\n(?:(?!- id:)[\s\S])*/)?.[0]
      assert.ok(entry, 'native configuration must contain the installed PowerContext entry')
      assert.match(entry, /baseUrl: http:\/\/selected\.example/)
      assert.match(entry, /allowInsecureHttp: true/)
      const current = readFileSync(fixture.manifest)
      const bundles = JSON.parse(current).dsh.profile.bundles
      assert.equal(bundles.filter(name => name === 'powercontext-dsh').length, 1)
      assert.equal(bundles.filter(name => name === 'transport-override').length, 1)
      if (manifest) assert.deepEqual(current, manifest)
      manifest = current
      for (const [index, path] of patches.entries()) assert.deepEqual(readFileSync(path), original[index])
    }
  } finally {
    assert.equal(dirname(resolve(home)), resolve(tmpdir()))
    rmSync(home, { recursive: true, force: true })
  }
})
