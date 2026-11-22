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
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { generatePythonApi } from '../scripts/generate-python-api';

test('Python API generation reads source without importing runtime dependencies', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pc-python-api-'));
  try {
    const source = path.join(root, 'src');
    const output = path.join(root, 'output');
    await mkdir(source);
    await mkdir(output);
    await writeFile(path.join(source, 'sample_api.py'), `
import missing_runtime_dependency
raise RuntimeError("API documentation must not import this module")

def greet(name: str) -> str:
    """Return a greeting.

    Args:
        name: The person to greet.
    """
    return "Hello " + name
`);
    generatePythonApi(source, output, ['sample_api']);
    const result = JSON.parse(await readFile(path.join(output, 'sample_api.json'), 'utf8'));
    assert.equal(result.path, 'sample_api');
    assert.equal(result.functions.greet.name, 'greet');
    assert.equal(result.functions.greet.description, 'Return a greeting.');
    assert.equal(result.functions.greet.parameters[0].name, 'name');
    assert.deepEqual(result.functions.greet.parameters[0].description, [{ kind: 'text', value: 'The person to greet.' }]);
    assert.match(result.functions.greet.source, /def greet\(name: str\) -> str:/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
