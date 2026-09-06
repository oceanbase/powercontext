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

import { copyFile, cp, mkdir, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const websiteDir = path.resolve(scriptDir, '..');
const repositoryDir = path.resolve(websiteDir, '..');
const generatedDir = path.join(websiteDir, '.generated');
const pythonDir = path.join(generatedDir, 'python');
const generatedDocsDir = path.join(websiteDir, 'content', 'docs');
const publicDir = path.join(websiteDir, 'public');

function formatRfcTitle(fileName: string, content: string) {
  const number = fileName.match(/^(\d{4})_/)?.[1] ?? fileName.replace(/\.md$/, '');
  const proposalName = content.match(/^- Proposal Name:\s*`([^`]+)`/m)?.[1]
    ?? fileName.replace(/^\d{4}_|\.md$/g, '');
  const acronyms: Record<string, string> = {
    ai: 'AI',
    api: 'API',
    http: 'HTTP',
    llm: 'LLM',
    mcp: 'MCP',
    rbac: 'RBAC',
    rest: 'REST',
    sdk: 'SDK',
    ui: 'UI',
  };
  const minorWords = new Set(['and', 'for', 'of', 'or', 'the', 'to']);
  const title = proposalName
    .split('_')
    .map((word, index) => acronyms[word] ?? (index > 0 && minorWords.has(word)
      ? word
      : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(' ');

  return `RFC ${number}: ${title}`;
}

async function prepareRfcContent(locale: string) {
  const rfcDir = path.join(generatedDocsDir, locale, 'rfcs');
  await rename(path.join(rfcDir, 'README.md'), path.join(rfcDir, 'index.md'));

  const entries = await readdir(rfcDir, { withFileTypes: true });
  await Promise.all(
    entries
      .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
      .map(async (entry) => {
        const filePath = path.join(rfcDir, entry.name);
        const content = await readFile(filePath, 'utf8');
        if (content.startsWith('---\n')) return;

        const title = formatRfcTitle(entry.name, content);
        await writeFile(filePath, `---\ntitle: ${JSON.stringify(title)}\n---\n\n${content}`);
      }),
  );
}

await Promise.all([
  rm(pythonDir, { recursive: true, force: true }),
  rm(generatedDocsDir, { recursive: true, force: true }),
]);

await Promise.all([
  mkdir(pythonDir, { recursive: true }),
  mkdir(publicDir, { recursive: true }),
]);

await Promise.all([
  cp(path.join(repositoryDir, 'docs', 'en', 'docs'), path.join(generatedDocsDir, 'en', 'docs'), {
    recursive: true,
    force: true,
  }),
  cp(path.join(repositoryDir, 'docs', 'zh', 'docs'), path.join(generatedDocsDir, 'zh', 'docs'), {
    recursive: true,
    force: true,
  }),
  cp(path.join(repositoryDir, 'docs', 'en', 'rfcs'), path.join(generatedDocsDir, 'en', 'rfcs'), {
    recursive: true,
    force: true,
  }),
  cp(path.join(repositoryDir, 'docs', 'zh', 'rfcs'), path.join(generatedDocsDir, 'zh', 'rfcs'), {
    recursive: true,
    force: true,
  }),
  cp(path.join(repositoryDir, 'docs', 'assets'), path.join(generatedDocsDir, 'assets'), {
    recursive: true,
    force: true,
  }),
]);

await Promise.all(['en', 'zh'].map(prepareRfcContent));

const pythonPackage = path.join(websiteDir, 'node_modules', 'fumadocs-python');
const pythonModules = [
  'powercontext.context',
  'powercontext.sources',
  'powercontext.artifacts',
  'powercontext.builtin.artifacts.memory',
  'powercontext.triggers',
  'powercontext.errors',
  'powercontext.client',
];

for (const moduleName of pythonModules) {
  const result = spawnSync(
    'uv',
    [
      'run',
      '--project',
      repositoryDir,
      '--with',
      pythonPackage,
      'fumapy-generate',
      moduleName,
      '--dir',
      pythonDir,
    ],
    { cwd: repositoryDir, stdio: 'inherit' },
  );

  if (result.status !== 0) {
    throw new Error(`Python API generation for ${moduleName} failed with status ${result.status ?? 'unknown'}`);
  }
}

await Promise.all([
  copyFile(
    path.join(repositoryDir, 'src', 'powercontext', 'server', 'static', 'powercontext-color.png'),
    path.join(publicDir, 'powercontext-color.png'),
  ),
  copyFile(
    path.join(repositoryDir, 'src', 'powercontext', 'server', 'static', 'powercontext-reverse.png'),
    path.join(publicDir, 'powercontext-reverse.png'),
  ),
]);
