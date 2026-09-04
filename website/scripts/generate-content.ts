import { copyFile, cp, mkdir, rm } from 'node:fs/promises';
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
  cp(path.join(repositoryDir, 'docs', 'assets'), path.join(generatedDocsDir, 'assets'), {
    recursive: true,
    force: true,
  }),
]);

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
