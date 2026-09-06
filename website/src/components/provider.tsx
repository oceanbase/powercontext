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

'use client';

import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { usePathname, useRouter } from 'next/navigation';
import { defaultLanguage, i18nUI, type Language } from '@/lib/i18n';

export function Provider({ children, lang }: { children: ReactNode; lang: Language }) {
  const pathname = usePathname();
  const router = useRouter();
  const provider = i18nUI.provider(lang);

  function onLocaleChange(nextLanguage: string) {
    const segments = pathname.split('/').filter(Boolean);
    if (segments[0] === lang) segments.shift();

    if (segments.length === 0 && nextLanguage === defaultLanguage) {
      router.push('/');
      return;
    }

    router.push(`/${[nextLanguage, ...segments].join('/')}`);
  }

  return (
    <RootProvider i18n={{ ...provider, onLocaleChange }} search={{ enabled: false }}>
      {children}
    </RootProvider>
  );
}
