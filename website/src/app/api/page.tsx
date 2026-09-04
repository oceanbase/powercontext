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
