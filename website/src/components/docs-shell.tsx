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

import type { CSSProperties, ReactNode } from 'react';
import type { Root } from 'fumadocs-core/page-tree';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { SiteFooter } from '@/components/site-footer';
import type { Language } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';

/**
 * A documentation section under the site's own top bar and footer, so docs,
 * the HTTP API and the Python API share the chrome of every other page. The
 * sidebar holds only the section's page tree: search, language, theme and
 * the site links already live in the top bar, which the sidebar and table of
 * contents stick beneath.
 */
export function DocsShell({ lang, tree, title, url, children }: {
  lang: Language;
  tree: Root;
  /** The section's name, shown above its sidebar and in the phone bar. */
  title: string;
  url: string;
  children: ReactNode;
}) {
  return (
    <HomeLayout {...baseOptions(lang)}>
      <DocsLayout
        containerProps={{ className: 'pc-docs', style: { '--fd-banner-height': '3.5rem' } as CSSProperties }}
        i18n={false}
        links={[]}
        nav={{ title: <span className="pc-docs-section">{title}</span>, url }}
        searchToggle={{ enabled: false }}
        themeSwitch={{ enabled: false }}
        tree={tree}
      >
        {children}
      </DocsLayout>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}
