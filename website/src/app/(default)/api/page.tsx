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

import { ArrowRight } from 'lucide-react';
import { DocsBody, DocsDescription, DocsPage, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { Card, Cards } from 'fumadocs-ui/components/card';
import { apiSource } from '@/lib/openapi-source';

export default function ApiOverviewPage() {
  const pages = apiSource.getPages();

  return (
    <DocsPage toc={[]}>
      <DocsTitle>HTTP API reference</DocsTitle>
      <DocsDescription>
        Endpoints and schemas exposed by PowerContext Server. The reference is generated from
        {' '}<code>openapi/powercontext.yaml</code>, the source of truth for the HTTP contract.
      </DocsDescription>
      <DocsBody>
        <Cards>
          {pages.slice(0, 12).map((page) => (
            <Card href={page.url} icon={<ArrowRight />} key={page.url} title={page.data.title} />
          ))}
        </Cards>
        <p>Use the sidebar to browse all {pages.length} operations.</p>
      </DocsBody>
    </DocsPage>
  );
}
