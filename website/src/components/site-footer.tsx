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

import { SiteFooterShell } from '@astrafoundry/design/ui';
import Link from 'next/link';
import { BrandLogo } from './brand-logo';
import { FooterHelix } from './home/footer-helix';
import { defaultLanguage, type Language } from '@/lib/i18n';
import { repositoryUrl } from '@/lib/urls';

const copy = {
  en: {
    description: 'Project context that carries knowledge and active work across sessions.',
    product: 'Product',
    resources: 'Resources',
    docs: 'Docs',
    benchmarks: 'Benchmarks',
    changelog: 'Changelog',
    python: 'Python API',
    http: 'HTTP API',
    maintained: 'Maintained by',
  },
  zh: {
    description: '让项目知识和当前任务跨会话延续的上下文运行层。',
    product: '产品',
    resources: '资源',
    docs: '文档',
    benchmarks: '基准测试',
    changelog: '更新日志',
    python: 'Python API',
    http: 'HTTP API',
    maintained: '维护方',
  },
} as const;

/**
 * The footer every page shares: Sunbeam's SiteFooterShell from
 * @astrafoundry/design, closing on the helix drawn from the brand mark.
 */
export function SiteFooter({ lang }: { lang: Language }) {
  const text = copy[lang];

  return (
    <SiteFooterShell
      brand={(
        <Link aria-label="PowerContext" className="w-fit" href={lang === defaultLanguage ? '/' : `/${lang}`}>
          <BrandLogo className="w-40" />
        </Link>
      )}
      className="pc-site-footer"
      columns={[
        {
          title: text.product,
          links: [
            { label: text.docs, href: `/${lang}/docs` },
            { label: text.benchmarks, href: `/${lang}/benchmarks` },
            { label: text.changelog, href: `/${lang}/changelog` },
          ],
        },
        {
          title: text.resources,
          links: [
            { label: text.python, href: `/${lang}/modules` },
            { label: text.http, href: '/api' },
            { label: 'GitHub', href: repositoryUrl },
          ],
        },
      ]}
      illustration={<FooterHelix />}
      legal={(
        <>
          {text.maintained}{' '}
          <a className="text-foreground hover:underline" href="https://oceanbase.com" rel="noreferrer" target="_blank">OceanBase</a>
        </>
      )}
      linkAs={Link}
      tagline={text.description}
    />
  );
}
