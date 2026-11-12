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

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { parse } from 'yaml';
import type { Language } from './i18n';

type HomeAction = {
  href: string;
  label: string;
};

export type MemoryState = 'active' | 'forgotten' | 'merged' | 'retired';

export type HomeSessionWindow = {
  title: string;
  action: string;
  action_done: string;
  result_label: string;
};

export type HomeContent = {
  onboarding: {
    title: string;
    lead: string;
    preview_label: string;
    preview_note: string;
    repository_label: string;
    guide_label: string;
    copy_label: string;
    copied_label: string;
    steps: Array<{
      title: string;
      description: string;
      command?: string;
    }>;
  };
  continuity: {
    lead: string;
    steps: Array<{
      items: string[];
      title: string;
    }>;
    title: string;
    visual_label: string;
    dashboard: {
      title: string;
      scope: string;
      list_label: string;
      revision_label: string;
      current_label: string;
      state_label: string;
      sources_label: string;
      states: Record<MemoryState, string>;
      entries: Array<{
        id: string;
        title: string;
        revision: number;
        state: MemoryState;
      }>;
      body: string;
      sources: string[];
    };
  };
  /** Photo credit template with `{author}` and `{source}` placeholders. */
  photo_credit: string;
  hero: {
    action: HomeAction;
    learn_more: HomeAction;
    community: {
      star_label: string;
      contributors_label: string;
      contribute_label: string;
      license_label: string;
    };
    cursors: {
      visitor: string;
      claude_code: string;
      codex: string;
      opencode: string;
    };
    figure: {
      description: string;
      figure: string;
      title: string;
      hint: string;
      readout: {
        from: string;
        to: string;
      };
      /** Work Contract, Prepared Handoff, Acknowledgement, Task Outcome. */
      steps: [string, string, string, string];
    };
    sessions: {
      ending: HomeSessionWindow & {
        lines: string[];
        result: string;
      };
      next: HomeSessionWindow & {
        prompt: string;
        waiting: string;
        received: Array<{
          label: string;
          value: string;
        }>;
      };
    };
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
    status: {
      official: string;
      community: string;
      evaluation: string;
    };
    title: string[];
    visual_label: string;
  };
};

type HomeFrontmatter = {
  home?: HomeContent;
};

export async function getHomeContent(lang: Language): Promise<HomeContent> {
  const sourcePath = path.resolve(process.cwd(), '..', 'docs', lang, 'index.md');
  const source = (await readFile(sourcePath, 'utf8')).replace(/\r\n/g, '\n');
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
