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
import { test } from 'node:test';
import { createGitHubStarsLoader, githubRepository } from '../src/lib/github-stars';

const url = 'https://github.com/oceanbase/powercontext';
const snapshot = { repository: 'oceanbase/powercontext', count: 985, updatedAt: 1000 };

test('accepts the configured GitHub repository, not a lookalike host or nested path', async () => {
  assert.equal(githubRepository('https://github.com/Example/Project.git/'), 'example/project');
  const load = createGitHubStarsLoader(async () => { assert.fail('Invalid URLs must not cause requests'); });
  for (const invalid of ['bad-url', 'http://github.com/a/b', 'https://github.com.evil.test/a/b', 'https://github.com/a/b/issues']) {
    assert.equal(githubRepository(invalid), null);
    assert.equal(await load(invalid), null);
  }
});

test('loads one static snapshot across concurrent navigation instances and subsequent visits', async () => {
  let requests = 0;
  const load = createGitHubStarsLoader(async (input, init) => {
    assert.equal(input, 'https://raw.githubusercontent.com/oceanbase/powercontext/website-stats/github-stars.json');
    assert.equal(init?.credentials, 'omit');
    assert.ok(init?.signal instanceof AbortSignal);
    requests++;
    return Response.json(snapshot);
  });
  assert.deepEqual(await Promise.all([load(url), load(url)]), [snapshot, snapshot]);
  assert.deepEqual(await load(url), snapshot);
  assert.equal(requests, 1, 'one CDN request per page session; no GitHub API polling');
});

test('missing snapshots and network failures fall back without retries or invented counts', async () => {
  for (const response of [
    async () => new Response(null, { status: 404 }),
    async () => new Response('invalid JSON'),
    async () => { throw new TypeError('Offline'); },
  ]) {
    let requests = 0;
    const load = createGitHubStarsLoader(async () => { requests++; return response(); });
    assert.equal(await load(url), null);
    assert.equal(await load(url), null);
    assert.equal(requests, 1);
  }
});

test('validates repository, count and timestamp while accepting a real zero count', async () => {
  for (const data of [
    null, { ...snapshot, repository: 'example/another' }, { ...snapshot, updatedAt: 0 },
    { ...snapshot, updatedAt: Date.now() + 60_000 }, { ...snapshot, updatedAt: '1000' },
    ...[-1, 1.5, '985', null, Number.MAX_SAFE_INTEGER + 1].map((count) => ({ ...snapshot, count })),
  ]) {
    assert.equal(await createGitHubStarsLoader(async () => Response.json(data))(url), null);
  }
  const zero = { ...snapshot, count: 0 };
  assert.deepEqual(await createGitHubStarsLoader(async () => Response.json(zero))(url), zero);
});
