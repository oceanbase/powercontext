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
import { createGitHubStarsClient, githubRepository, starRefreshInterval } from '../src/lib/github-stars';

const url = 'https://github.com/oceanbase/powercontext';
const key = 'powercontext:github-stars:oceanbase/powercontext';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (name: string) => values.get(name) ?? null,
    setItem: (name: string, value: string) => { values.set(name, value); },
  };
}

test('uses the configured GitHub repository, not an unrelated or lookalike host', () => {
  assert.equal(githubRepository('https://github.com/Example/Project.git/'), 'example/project');
  for (const invalid of ['bad-url', 'http://github.com/a/b', 'https://github.com.evil.test/a/b', 'https://github.com/a/b/issues']) {
    assert.equal(githubRepository(invalid), null);
  }
});

test('shares requests and cached counts across navigation instances, then refreshes', async () => {
  let now = 1000;
  let requests = 0;
  const storage = memoryStorage();
  const client = createGitHubStarsClient({
    now: () => now,
    storage: () => storage,
    fetcher: async (input) => {
      assert.equal(input, 'https://api.github.com/repos/oceanbase/powercontext');
      return Response.json({ stargazers_count: 984 + ++requests });
    },
  });
  assert.deepEqual(await Promise.all([client.load(url), client.load(url)]), [
    { count: 985, updatedAt: 1000 }, { count: 985, updatedAt: 1000 },
  ]);
  await client.load(url);
  assert.equal(requests, 1, 'one unauthenticated API request per refresh window');
  now += starRefreshInterval;
  assert.equal((await client.load(url))?.count, 986);
  assert.equal(requests, 2);
  assert.deepEqual(JSON.parse(storage.getItem(key)!), { count: 986, updatedAt: now });
});

test('restores a fresh session cache without a network request', async () => {
  const storage = memoryStorage();
  storage.setItem(key, JSON.stringify({ count: 1234, updatedAt: 1000 }));
  const client = createGitHubStarsClient({
    storage: () => storage,
    now: () => 2000,
    fetcher: async () => { throw new Error('Should use the fresh cache'); },
  });
  assert.deepEqual(client.peek(url), { count: 1234, updatedAt: 1000 });
  assert.deepEqual(await client.load(url), { count: 1234, updatedAt: 1000 });
});

test('keeps the last successful count and timestamp on network errors and rate limits', async () => {
  for (const fetcher of [
    async () => { throw new TypeError('Network unavailable'); },
    async () => new Response(null, { status: 403 }),
    async () => new Response(null, { status: 429 }),
  ]) {
    const storage = memoryStorage();
    storage.setItem(key, JSON.stringify({ count: 985, updatedAt: 1000 }));
    const client = createGitHubStarsClient({ storage: () => storage, now: () => starRefreshInterval + 1000, fetcher });
    assert.deepEqual(await client.load(url), { count: 985, updatedAt: 1000 });
    assert.deepEqual(JSON.parse(storage.getItem(key)!), { count: 985, updatedAt: 1000 });
  }
});

test('first-load failures return no invented count and are throttled until retry', async () => {
  let now = 1000;
  let requests = 0;
  const client = createGitHubStarsClient({
    now: () => now,
    storage: memoryStorage,
    fetcher: async () => {
      requests++;
      if (requests === 1) throw new TypeError('Offline');
      return Response.json({ stargazers_count: 42 });
    },
  });
  assert.equal(await client.load(url), null);
  assert.equal(await client.load(url), null);
  assert.equal(requests, 1);
  now += starRefreshInterval;
  assert.equal((await client.load(url))?.count, 42);
});

test('times out a stalled request and recovers on the next refresh', async () => {
  let now = 1000;
  let stalled = true;
  const client = createGitHubStarsClient({
    now: () => now,
    timeout: 10,
    storage: memoryStorage,
    fetcher: async (_input, init) => stalled
      ? new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new Error('Aborted')), { once: true });
      })
      : Response.json({ stargazers_count: 986 }),
  });
  assert.equal(await client.load(url), null);
  stalled = false;
  now += starRefreshInterval;
  assert.equal((await client.load(url))?.count, 986);
});

test('rejects malformed counts but accepts zero, including when storage is disabled', async () => {
  for (const count of [-1, 1.5, '985', null, Number.MAX_SAFE_INTEGER + 1, 0]) {
    const client = createGitHubStarsClient({
      now: () => 1000,
      storage: () => { throw new Error('Storage disabled'); },
      fetcher: async () => Response.json({ stargazers_count: count }),
    });
    assert.equal((await client.load(url))?.count ?? null, count === 0 ? 0 : null);
    if (count === 0) assert.equal(client.peek(url)?.count, 0);
  }
});

test('ignores corrupt or future-dated cache data and isolates repositories', async () => {
  for (const cached of ['{broken', JSON.stringify({ count: -1, updatedAt: 1000 }), JSON.stringify({ count: 12, updatedAt: 5000 })]) {
    const storage = memoryStorage();
    storage.setItem(key, cached);
    const client = createGitHubStarsClient({
      storage: () => storage,
      now: () => 2000,
      fetcher: async () => Response.json({ stargazers_count: 985 }),
    });
    assert.equal(client.peek(url), null);
    assert.equal((await client.load(url))?.count, 985);
    assert.equal(client.peek('https://github.com/example/another'), null);
  }
});
