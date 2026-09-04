import { ExternalLink } from 'lucide-react';
import type { ReactNode } from 'react';

export function ExternalTextLink({ children, href }: { children: ReactNode; href: string }) {
  return (
    <span className="prose">
      <a href={href} rel="noreferrer" target="_blank">
        {children} <ExternalLink aria-hidden="true" className="inline size-4" />
      </a>
    </span>
  );
}
