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
