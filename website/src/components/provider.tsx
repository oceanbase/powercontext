'use client';

import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { i18nUI, type Language } from '@/lib/i18n';

export function Provider({ children, lang }: { children: ReactNode; lang: Language }) {
  return (
    <RootProvider i18n={i18nUI.provider(lang)} search={{ enabled: false }}>
      {children}
    </RootProvider>
  );
}
