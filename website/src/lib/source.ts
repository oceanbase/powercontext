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

import { loader } from 'fumadocs-core/source';
import { metaSchema, pageSchema } from 'fumadocs-core/source/schema';
import { statusBadgesPlugin } from 'fumadocs-core/source/status-badges';
import { defineDocs } from 'fumadocs-mdx/macro';
import { createElement } from 'react';
import { i18n } from './i18n';

const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    schema: pageSchema.extend({ status: pageSchema.shape.title.optional() }),
    postprocess: {
      includeProcessedMarkdown: true,
    },
  },
  meta: {
    schema: metaSchema,
  },
});

export const source = loader({
  baseUrl: '/',
  i18n,
  source: docs.toFumadocsSource(),
  plugins: [statusBadgesPlugin({
    renderBadge: (status) => createElement('span', {
      className: 'ms-auto rounded border px-1 text-[10px] font-normal text-fd-muted-foreground',
      'data-status': status,
    }, status),
  })],
});
