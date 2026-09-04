import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { DocsBody, DocsPage, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import Link from 'next/link';
import { Card, Cards } from 'fumadocs-ui/components/card';
import { isLanguage, languages } from '@/lib/i18n';
import { getPythonSource, pythonModules } from '@/lib/python-source';

interface PageProps {
  params: Promise<{ lang: string; slug?: string[] }>;
}

function getPythonPage(lang: string, slug: string[] = []) {
  const pythonSource = getPythonSource(lang);
  return slug.length > 0 ? pythonSource.getPage(slug) : undefined;
}

export default async function PythonApiPage({ params }: PageProps) {
  const { lang, slug } = await params;
  if (!isLanguage(lang)) notFound();
  if (!slug || slug.length === 0) {
    const title = lang === 'zh' ? 'Python API 参考' : 'Python API reference';
    const description = lang === 'zh'
      ? '当前 package 中公开 Python 模块的参考。RFC 可能包含尚未成为公开 API 的设计。'
      : 'Reference for public Python modules in the current package. RFCs may describe designs that are not part of the public API.';
    const httpDescription = lang === 'zh'
      ? '服务端 endpoint 和 schema 请查 HTTP API 参考。'
      : 'Use the HTTP API reference for Server endpoints and schemas.';

    return (
      <DocsPage toc={[]}>
        <DocsTitle>{title}</DocsTitle>
        <DocsBody>
          <p>{description}</p>
          <Cards>
            {pythonModules.map((moduleName) => (
              <Card
                href={`/${lang}/modules/${moduleName.replaceAll('.', '/')}`}
                key={moduleName}
                title={moduleName}
              />
            ))}
          </Cards>
          <p><Link href="/api">{httpDescription}</Link></p>
        </DocsBody>
      </DocsPage>
    );
  }
  const page = getPythonPage(lang, slug);
  if (!page) notFound();
  const renderer = await page.data.load();
  const rendered = await renderer.render();

  return (
    <DocsPage toc={rendered.toc}>
      <DocsTitle>{page.data.title}</DocsTitle>
      <DocsBody>{rendered.body}</DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return languages.flatMap((lang) => {
    const pythonSource = getPythonSource(lang);
    return [{ lang, slug: [] }, ...pythonSource.getPages().map((page) => ({ lang, slug: page.slugs }))];
  });
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { lang, slug } = await params;
  if (!slug || slug.length === 0) return { title: 'Python API' };
  const page = getPythonPage(lang, slug);
  if (!page) notFound();

  return { title: `${page.data.title} Python API` };
}
