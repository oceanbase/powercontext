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

import { access, readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { parse } from 'yaml';

const websiteDirectory = path.resolve('.');
const repositoryDirectory = path.resolve(websiteDirectory, '..');
const outputDirectory = path.join(websiteDirectory, 'out');
const prerenderManifestPath = path.join(websiteDirectory, '.next', 'prerender-manifest.json');
const pythonDirectory = path.join(websiteDirectory, '.generated', 'python');
const locales = ['en', 'zh'] as const;
const httpMethods = ['get', 'put', 'post', 'delete', 'patch', 'head', 'options', 'trace'] as const;

type PythonModule = {
  path?: string;
  modules?: Record<string, PythonModule>;
  classes?: Record<string, { path?: string }>;
};

function outputPage(route: string) {
  const segments = route.split('/').filter(Boolean);
  return path.join(outputDirectory, ...segments, 'index.html');
}

function collectPythonPaths(module: PythonModule, paths: Set<string>) {
  if (module.path) paths.add(module.path.replaceAll('.', '/'));

  for (const classDefinition of Object.values(module.classes ?? {})) {
    if (classDefinition.path) paths.add(classDefinition.path.replaceAll('.', '/'));
  }

  for (const childModule of Object.values(module.modules ?? {})) {
    collectPythonPaths(childModule, paths);
  }
}

const openapi = parse(
  await readFile(path.join(repositoryDirectory, 'openapi', 'powercontext.yaml'), 'utf8'),
) as {
  paths?: Record<string, Record<string, { operationId?: string }>>;
};

const httpRoutes = new Set<string>(['/api']);
for (const pathItem of Object.values(openapi.paths ?? {})) {
  for (const method of httpMethods) {
    const operationId = pathItem[method]?.operationId;
    if (!operationId) continue;
    httpRoutes.add(`/api/${operationId}`);
  }
}

const pythonPaths = new Set<string>();
const pythonFiles = (await readdir(pythonDirectory)).filter((file) => file.endsWith('.json'));
for (const file of pythonFiles) {
  const module = JSON.parse(await readFile(path.join(pythonDirectory, file), 'utf8')) as PythonModule;
  collectPythonPaths(module, pythonPaths);
}

const pythonRoutes = new Set<string>();
for (const locale of locales) {
  pythonRoutes.add(`/${locale}/modules`);
  for (const pythonPath of pythonPaths) {
    pythonRoutes.add(`/${locale}/modules/${pythonPath}`);
  }
}

const prerenderManifest = JSON.parse(await readFile(prerenderManifestPath, 'utf8')) as {
  routes: Record<string, unknown>;
};
const prerenderedRoutes = new Set(Object.keys(prerenderManifest.routes));
const expectedRoutes = [...httpRoutes, ...pythonRoutes];
const missingFromManifest = expectedRoutes.filter((route) => !prerenderedRoutes.has(route));
const missingFromOutput: string[] = [];

await Promise.all(
  expectedRoutes.map(async (route) => {
    try {
      await access(outputPage(route));
    } catch {
      missingFromOutput.push(route);
    }
  }),
);

if (missingFromManifest.length > 0 || missingFromOutput.length > 0) {
  const details = [
    missingFromManifest.length > 0
      ? `Missing from Next prerender manifest:\n${missingFromManifest.sort().join('\n')}`
      : undefined,
    missingFromOutput.length > 0
      ? `Missing from static output:\n${missingFromOutput.sort().join('\n')}`
      : undefined,
  ].filter(Boolean);

  throw new Error(`Static API export is incomplete.\n\n${details.join('\n\n')}`);
}

console.log(
  `Verified ${httpRoutes.size} HTTP API pages and ${pythonRoutes.size} Python API pages in the static export.`,
);
