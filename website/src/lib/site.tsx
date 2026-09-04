import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { BrandLogo } from '@/components/brand-logo';
import type { Language } from './i18n';

const labels = {
  en: {
    docs: 'Docs',
    benchmarks: 'Benchmarks',
    changelog: 'Changelog',
  },
  zh: {
    docs: '文档',
    benchmarks: '基准测试',
    changelog: '更新日志',
  },
} as const;

export function baseOptions(lang: Language): BaseLayoutProps {
  const label = labels[lang];

  return {
    nav: {
      title: <BrandLogo className="w-36" priority />,
      url: `/${lang}`,
    },
    links: [
      { text: label.docs, url: `/${lang}/docs` },
      { text: label.benchmarks, url: `/${lang}/benchmarks` },
      { text: label.changelog, url: `/${lang}/changelog` },
      { text: 'GitHub', url: 'https://github.com/oceanbase/powercontext', external: true },
    ],
  };
}
