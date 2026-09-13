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
import { updateGitHubStars } from '../scripts/update-github-stars.mjs';

const repository = { owner: 'oceanbase', repo: 'powercontext' };
const initial = { repository: 'oceanbase/powercontext', count: 985, updatedAt: 1000 };
const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64');

function fixture(saved = initial) {
  const state = { saved, count: 986, writes: [], tree: [], parents: null };
  const github = { rest: {
    repos: {
      get: async () => ({ data: { stargazers_count: state.count } }),
      getContent: async () => ({ data: { type: 'file', content: encode(state.saved), sha: 'previous' } }),
      createOrUpdateFileContents: async (file) => {
        state.writes.push(file);
        state.saved = JSON.parse(Buffer.from(file.content, 'base64').toString('utf8'));
      },
    },
    git: {
      getRef: async () => {
        if (!state.saved) throw Object.assign(new Error('Not found'), { status: 404 });
        return { data: {} };
      },
      createTree: async ({ tree }) => { state.tree = tree; return { data: { sha: 'tree' } }; },
      createCommit: async ({ parents }) => { state.parents = parents; return { data: { sha: 'commit' } }; },
      createRef: async (ref) => {
        state.writes.push(ref);
        state.saved = JSON.parse(state.tree[0].content);
      },
    },
  } };
  return { github, state };
}

test('bootstraps a data-only branch without source files or source history', async () => {
  const { github, state } = fixture(null);
  await updateGitHubStars(github, repository, 2000);
  assert.deepEqual(state.saved, { ...initial, count: 986, updatedAt: 2000 });
  assert.deepEqual(state.tree.map(({ path }) => path), ['github-stars.json']);
  assert.deepEqual(state.parents, []);
  assert.equal(state.writes[0].ref, 'refs/heads/website-stats');
});

test('updates only the snapshot branch and accepts a real zero count', async () => {
  const { github, state } = fixture();
  state.count = 0;
  assert.deepEqual(await updateGitHubStars(github, repository, 2000), { ...initial, count: 0, updatedAt: 2000 });
  assert.deepEqual(state.saved, { ...initial, count: 0, updatedAt: 2000 });
  assert.equal(state.writes[0].branch, 'website-stats');
  assert.equal(state.writes[0].path, 'github-stars.json');
  assert.equal(state.writes[0].sha, 'previous', 'concurrent changes must not be overwritten blindly');
});

test('skips unchanged snapshots within a day and refreshes their timestamp after a day', async () => {
  const { github, state } = fixture();
  state.count = initial.count;
  assert.deepEqual(await updateGitHubStars(github, repository, 2000), initial);
  assert.equal(state.writes.length, 0, 'unchanged runs must be idempotent');
  const tomorrow = 1000 + 24 * 60 * 60 * 1000;
  await updateGitHubStars(github, repository, tomorrow);
  assert.deepEqual(state.saved, { ...initial, updatedAt: tomorrow });
  assert.equal(state.writes.length, 1);
});

test('invalid GitHub counts and rate limits fail without touching the last published snapshot', async () => {
  for (const count of [-1, 1.5, '986', null, Number.MAX_SAFE_INTEGER + 1]) {
    const { github, state } = fixture();
    state.count = count;
    await assert.rejects(updateGitHubStars(github, repository, 2000), /invalid Star count/);
    assert.deepEqual(state.saved, initial);
    assert.equal(state.writes.length, 0);
  }
  const { github, state } = fixture();
  github.rest.repos.get = async () => { throw Object.assign(new Error('Rate limited'), { status: 429 }); };
  await assert.rejects(updateGitHubStars(github, repository), /Rate limited/);
  assert.deepEqual(state.saved, initial);
  assert.equal(state.writes.length, 0);
});

test('permission errors do not trigger branch creation or overwrite published data', async () => {
  const { github, state } = fixture();
  github.rest.git.getRef = async () => { throw Object.assign(new Error('Forbidden'), { status: 403 }); };
  await assert.rejects(updateGitHubStars(github, repository), /Forbidden/);
  assert.equal(state.writes.length, 0);
  assert.deepEqual(state.saved, initial);
});
