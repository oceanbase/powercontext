import type { ReactNode } from 'react';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { Provider } from '@/components/provider';
import { apiSource } from '@/lib/openapi-source';
import { baseOptions } from '@/lib/site';

export default function ApiLayout({ children }: { children: ReactNode }) {
  return (
    <Provider lang="en">
      <DocsLayout tree={apiSource.getPageTree()} {...baseOptions('en')}>
        {children}
      </DocsLayout>
    </Provider>
  );
}
