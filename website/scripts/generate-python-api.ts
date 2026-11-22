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

import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const projectDir = fileURLToPath(new URL('../python/', import.meta.url));

export function generatePythonApi(sourceDir: string, outputDir: string, modules: string[]) {
  for (const moduleName of modules) {
    const result = spawnSync(
      'uv',
      ['run', '--locked', '--project', projectDir, 'fumapy-generate', moduleName, '--dir', outputDir],
      {
        stdio: 'inherit',
        // Griffe reads the source tree statically; runtime dependencies must not
        // share the generator's environment and overwrite its Griffe package.
        env: { ...process.env, PYTHONPATH: sourceDir },
      },
    );
    if (result.status !== 0) {
      throw new Error(`Python API generation for ${moduleName} failed with status ${result.status ?? 'unknown'}`);
    }
  }
}
