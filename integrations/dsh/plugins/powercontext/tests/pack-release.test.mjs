/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { test } from 'node:test'
import spawn from 'cross-spawn'

const root = resolve(import.meta.dirname, '..')

test('release archive installs and loads without checkout files or install scripts', { timeout: 120000 }, () => {
  const consumer = mkdtempSync(join(tmpdir(), 'powercontext dsh (consumer)-'))
  const original = readFileSync(join(root, 'package.json'), 'utf8')
  try {
    const version = process.env.POWERCONTEXT_DSH_PACKAGE_VERSION || '9.8.7-rc.2'
    const archive = process.env.POWERCONTEXT_DSH_TARBALL || join(consumer, JSON.parse(execFileSync(process.execPath,
      [join(root, 'scripts/pack-release.mjs'), version, consumer], { encoding: 'utf8' }))[0].filename)
    writeFileSync(join(consumer, 'package.json'), JSON.stringify({ private: true, type: 'module' }))
    const installation = spawn.sync('npm', ['install', archive, '@deepseek-ai/cordis@4.0.1',
      '--ignore-scripts', '--legacy-peer-deps', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org/'],
    { cwd: consumer, encoding: 'utf8', timeout: 90000 })
    assert.equal(installation.status, 0, installation.stderr || installation.error?.message)
    const installed = join(consumer, 'node_modules/powercontext-dsh')
    const manifest = JSON.parse(readFileSync(join(installed, 'package.json'), 'utf8'))
    assert.equal(manifest.name, 'powercontext-dsh')
    assert.equal(manifest.version, version)
    assert.equal(manifest.scripts, undefined)
    assert.equal(manifest.devDependencies, undefined)
    for (const file of [...manifest.files, 'README.md', 'LICENSE', manifest.dsh.bundle.patch]) {
      assert.ok(existsSync(join(installed, file)), `missing published asset: ${file}`)
    }
    for (const file of readdirSync(join(root, 'skills'), { recursive: true })) {
      const relative = join('skills', file)
      if (statSync(join(root, relative)).isFile()) {
        assert.deepEqual(readFileSync(join(installed, relative)), readFileSync(join(root, relative)),
          `missing or changed packaged skill resource: ${relative}`)
      }
    }
    for (const path of ['src', 'scripts', 'tests', 'node_modules', 'pnpm-lock.yaml']) {
      assert.equal(existsSync(join(installed, path)), false, `unexpected published file: ${path}`)
    }
    const output = execFileSync(process.execPath, ['--input-type=module', '-e', `
      import { name, Config } from 'powercontext-dsh';
      import * as invariant from 'powercontext-dsh/invariant';
      if (name !== 'powercontext-dsh' || Config['~standard'].validate({}).issues) process.exit(1);
      console.log(name);
    `], { cwd: consumer, encoding: 'utf8' })
    assert.equal(output.trim(), 'powercontext-dsh')
  } finally {
    assert.equal(readFileSync(join(root, 'package.json'), 'utf8'), original)
    rmSync(consumer, { recursive: true, force: true })
  }
})
