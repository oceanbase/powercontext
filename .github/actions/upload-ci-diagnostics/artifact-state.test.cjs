// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

const assert = require('node:assert/strict');
const {test} = require('node:test');
const artifactState = require('./artifact-state.cjs');

const digest = 'c'.repeat(64);
const complete = {id: 71, name: 'diagnostics-unique', expired: false, size_in_bytes: 156, digest: 'sha256:' + digest};

async function observe(mode, artifacts, baselineKnown = true, baselineIds = []) {
  const saved = {...process.env};
  const outputs = {};
  Object.assign(process.env, {
    DIAGNOSTIC_ARTIFACT_NAME: complete.name,
    DIAGNOSTIC_BASELINE_KNOWN: String(baselineKnown),
    DIAGNOSTIC_BASELINE_IDS: JSON.stringify(baselineIds),
    DIAGNOSTIC_RETRY_DELAY_MS: '0',
    GITHUB_SERVER_URL: 'https://github.com',
  });
  try {
    await artifactState({
      mode,
      context: {repo: {owner: 'owner', repo: 'repo'}, runId: 7},
      github: {
        rest: {actions: {listWorkflowRunArtifacts: {}}},
        paginate: async () => {
          if (artifacts instanceof Error) throw artifacts;
          return artifacts;
        },
      },
      core: {setOutput: (key, value) => {outputs[key] = value;}, warning: () => {}, info: () => {}},
    });
    return outputs;
  } finally {
    for (const key of Object.keys(process.env)) {
      if (!(key in saved)) delete process.env[key];
    }
    Object.assign(process.env, saved);
  }
}

test('an empty baseline is known, but a failed lookup stays unknown', async () => {
  assert.deepEqual(await observe('snapshot', []), {known: 'true', ids: '[]'});
  assert.deepEqual(await observe('snapshot', new Error('network')), {known: 'false'});
});

test('a new finalized archive preserves the original upload output contract', async () => {
  assert.deepEqual(await observe('recover', [complete]), {
    recovered: 'true',
    'artifact-id': 71,
    'artifact-url': 'https://github.com/owner/repo/actions/runs/7/artifacts/71',
    'artifact-digest': digest,
  });
});

test('an unknown baseline cannot recover an artifact', async () => {
  assert.deepEqual(await observe('recover', [complete], false), {recovered: 'false'});
});

test('a pre-existing same-name archive cannot hide an upload failure', async () => {
  assert.deepEqual(await observe('recover', [complete], true, [complete.id]), {recovered: 'false'});
});

test('an unfinished archive cannot become successful evidence', async () => {
  assert.deepEqual(await observe('recover', [{...complete, digest: null, size_in_bytes: 0}]), {recovered: 'false'});
});

test('a failed recovery lookup does not report success', async () => {
  assert.deepEqual(await observe('recover', new Error('network')), {recovered: 'false'});
});

test('multiple new same-name objects are ambiguous', async () => {
  assert.deepEqual(await observe('recover', [complete, {...complete, id: 72}]), {recovered: 'false'});
});

test('an expired archive cannot be recovered', async () => {
  assert.deepEqual(await observe('recover', [{...complete, expired: true}]), {recovered: 'false'});
});
