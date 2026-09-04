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

import path from 'node:path';
import { loader } from 'fumadocs-core/source';
import { createPython } from 'fumadocs-python';

export const pythonModules = [
  'powercontext.context',
  'powercontext.sources',
  'powercontext.artifacts',
  'powercontext.builtin.artifacts.memory',
  'powercontext.triggers',
  'powercontext.errors',
  'powercontext.client',
] as const;

const pythonSources = pythonModules.map((moduleName) =>
  createPython({ file: path.resolve(`.generated/python/${moduleName}.json`) }),
);

const staticSources = await Promise.all(pythonSources.map((python) => python.staticSource()));
const staticSource = {
  files: staticSources.flatMap((source) => source.files),
  configureStatic(options: Parameters<NonNullable<(typeof staticSources)[number]['configureStatic']>>[0]) {
    for (const source of staticSources) source.configureStatic?.(options);
  },
};

export function getPythonSource(locale: string) {
  return loader({
    baseUrl: `/${locale}/modules`,
    source: staticSource,
    plugins: [pythonSources[0].loaderPlugin()],
  });
}
