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

import Script from 'next/script';
import type { ReactNode } from 'react';
import { Provider } from '@/components/provider';
import { defaultLanguage, languagePreferenceKey } from '@/lib/i18n';
import { siteMetadata } from '@/lib/metadata';
import { withBasePath } from '@/lib/urls';
import '../global.css';

export const metadata = siteMetadata;

const languageRedirectScript = `
(() => {
  if (location.pathname !== ${JSON.stringify(withBasePath('/'))}) return;

  let savedLanguage;
  try {
    savedLanguage = localStorage.getItem(${JSON.stringify(languagePreferenceKey)});
  } catch {}

  const requestedLanguages = savedLanguage
    ? [savedLanguage]
    : navigator.languages?.length
      ? navigator.languages
      : [navigator.language];
  const preferredLanguage = requestedLanguages
    .map((language) => language.toLowerCase().split('-')[0])
    .find((language) => language === 'en' || language === 'zh');

  if (preferredLanguage === 'zh') {
    location.replace(${JSON.stringify(withBasePath('/zh/'))} + location.search + location.hash);
  }
})();
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Script
          dangerouslySetInnerHTML={{ __html: languageRedirectScript }}
          id="language-redirect"
          strategy="beforeInteractive"
        />
        <Provider lang={defaultLanguage}>{children}</Provider>
      </body>
    </html>
  );
}
