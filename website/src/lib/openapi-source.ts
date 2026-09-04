import path from 'node:path';
import { loader } from 'fumadocs-core/source';
import { createOpenAPI } from 'fumadocs-openapi/server';

export const openapi = createOpenAPI({
  input: [path.resolve('../openapi/powercontext.yaml')],
});

export const apiSource = loader({
  baseUrl: '/api',
  source: await openapi.staticSource({ meta: true }),
  plugins: [openapi.loaderPlugin()],
});
