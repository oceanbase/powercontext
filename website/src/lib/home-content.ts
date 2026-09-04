import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { parse } from 'yaml';
import type { Language } from './i18n';

type HomeAction = {
  href: string;
  kind?: 'primary' | 'secondary';
  label: string;
};

export type HomeContent = {
  continuity: {
    lead: string;
    steps: Array<{
      items: string[];
      title: string;
    }>;
    title: string;
    visual_label: string;
  };
  hero: {
    actions: HomeAction[];
    label: string;
    lead: string;
    title: string[];
  };
  ecosystem: {
    agents_label: string;
    all_agents_label: string;
    artifacts: Array<{
      description: string;
      name: string;
    }>;
    artifacts_label: string;
    docs_label: string;
    lead: string;
    output_label: string;
    runtime_label: string;
    title: string[];
    visual_label: string;
  };
};

type HomeFrontmatter = {
  home?: HomeContent;
};

export async function getHomeContent(lang: Language): Promise<HomeContent> {
  const sourcePath = path.resolve(process.cwd(), '..', 'docs', lang, 'index.md');
  const source = await readFile(sourcePath, 'utf8');
  const end = source.indexOf('\n---', 4);

  if (!source.startsWith('---\n') || end === -1) {
    throw new Error(`Home frontmatter is missing in ${sourcePath}`);
  }

  const frontmatter = parse(source.slice(4, end)) as HomeFrontmatter;
  if (!frontmatter.home) {
    throw new Error(`Home content is missing in ${sourcePath}`);
  }

  return frontmatter.home;
}
