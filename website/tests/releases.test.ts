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

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { parse } from 'yaml';
import { releases } from '../src/lib/releases';

test('the prepared stable release has a bilingual changelog with matching installation links', async (context) => {
  const contract = parse(await readFile(new URL('../../openapi/powercontext.yaml', import.meta.url), 'utf8'));
  const version = contract.info.version;
  assert.match(version, /^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$/);
  if (/(?:a|b|rc)\d+$/.test(version)) {
    context.skip('Prerelease notes are published on GitHub Releases, not the stable website changelog.');
    return;
  }

  const release = releases[0];
  assert.equal(
    release?.version,
    `v${version}`,
    `Add the v${version} entry to website/src/lib/releases.ts before publishing; preserve existing entries.`,
  );
  assert.equal(releases.filter((item) => item.version === release.version).length, 1);
  assert.equal(release.installCommand, `uv tool install --force "powercontext[cli,server]==${version}"`);
  assert.equal(release.githubUrl, `https://github.com/oceanbase/powercontext/releases/tag/powercontext-v${version}`);
  assert.match(release.date, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(new Date(`${release.date}T00:00:00Z`).toISOString().slice(0, 10), release.date);
  for (const language of ['en', 'zh'] as const) {
    assert.ok(release.title[language].trim(), `Missing ${language} title`);
    assert.ok(release.summary[language].trim(), `Missing ${language} summary`);
    assert.ok(release.changes[language].length > 0, `Missing ${language} release notes`);
    assert.ok(release.changes[language].every((change) => change.trim()), `Empty ${language} release note`);
  }
});
