import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import { DocsBody, DocsDescription, DocsPage, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { getMDXComponents } from '@/components/mdx';
import { isLanguage, languages } from '@/lib/i18n';
import { source } from '@/lib/source';

interface PageProps {
  params: Promise<{ lang: string; slug?: string[] }>;
}

function getDocumentationPage(lang: string, slug: string[] = []) {
  return source.getPage(['docs', ...slug], lang);
}

export default async function DocumentationPage({ params }: PageProps) {
  const { lang, slug } = await params;
  if (!isLanguage(lang)) notFound();
  const page = getDocumentationPage(lang, slug);
  if (!page) notFound();

  const MDX = page.data.body;
  const isOverview = !slug?.length;
  return (
    <DocsPage full={page.data.full} toc={page.data.toc}>
      {isOverview ? (
        <>
          <DocsTitle>{page.data.title}</DocsTitle>
          <DocsDescription>{page.data.description}</DocsDescription>
        </>
      ) : null}
      <DocsBody>
        <MDX components={getMDXComponents({ a: createRelativeLink(source, page) })} />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return languages.flatMap((lang) =>
    source
      .getPages(lang)
      .filter((page) => page.slugs[0] === 'docs')
      .map((page) => ({ lang, slug: page.slugs.slice(1) })),
  );
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { lang, slug } = await params;
  const page = getDocumentationPage(lang, slug);
  if (!page) notFound();

  return {
    title: page.data.title,
    description: page.data.description,
  };
}
