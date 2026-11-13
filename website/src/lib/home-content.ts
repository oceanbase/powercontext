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

type HomeField = {
  label: string;
  value: string;
};

export type HomeMemoryState = 'active' | 'forgotten' | 'merged' | 'retired';

export type HomeSessionWindow = {
  title: string;
  /** The depicted control the agent's cursor presses, before and after. */
  action: string;
  action_done: string;
};

type HomeFeature = {
  /** The card's caption. */
  title: string;
  /** Accessible description of the mock window. */
  label: string;
  /** The mock window's title bar. */
  window: string;
};

export type HomeContent = {
  hero: {
    title: string[];
    lead: string;
    action: HomeAction;
    learn_more: HomeAction;
    community: {
      star_label: string;
      contributors_label: string;
      contribute_label: string;
      license_label: string;
    };
    /** Name tags of the cursors that work in the hero demo. */
    cursors: {
      visitor: string;
      claude_code: string;
      codex: string;
    };
    /** Accessible description of the hero demo. */
    demo_label: string;
    sessions: {
      ending: HomeSessionWindow & {
        lines: string[];
        result_label: string;
        result: string;
      };
      next: HomeSessionWindow & {
        prompt: string;
        waiting: string;
        result_label: string;
        received: HomeField[];
      };
    };
    /** The Dashboard window between the two sessions: one Handoff and its four steps. */
    board: {
      title: string;
      scope: string;
      kind: string;
      heading: string;
      ref: string;
      revision_label: string;
      waiting_label: string;
      steps: Array<{ name: string; note: string }>;
      carried_label: string;
      carried: HomeField[];
    };
  };
  features: {
    title: string;
    lead: string;
    scope: string;
    memory: HomeFeature & {
      revision_label: string;
      state_label: string;
      sources_label: string;
      states: Record<HomeMemoryState, string>;
      entries: Array<{ id: string; title: string; revision: number; state: HomeMemoryState }>;
      body: string;
      sources: string[];
    };
    handoff: HomeFeature & {
      ref: string;
      fields: HomeField[];
    };
    review: HomeFeature & {
      kind: string;
      fields: HomeField[];
      approve: string;
      reject: string;
      /** Shown once the depicted approval lands. */
      approved: string;
    };
    skill: HomeFeature & {
      name: string;
      description: string;
      checks_label: string;
      checks: string[];
      status: string;
      export: string;
    };
    recall: HomeFeature & {
      status: string;
      budget_label: string;
      budget: string;
      /** `share` is the section's part of the budget, in percent. */
      sections: Array<{ name: string; size: string; share: number }>;
    };
  };
  ecosystem: {
    title: string[];
    lead: string;
    docs_label: string;
    all_agents_label: string;
    status: Record<'official' | 'community' | 'evaluation', string>;
    setup_title: string;
    setup_lead: string;
    interfaces_title: string;
    interfaces: Array<{ name: string; description: string; href: string }>;
  };
  proof: {
    title: string;
    lead: string;
    stats: Array<{ value: string; label: string; detail: string }>;
    link: HomeAction;
  };
  onboarding: {
    title: string;
    lead: string;
    guide_label: string;
    copy_label: string;
    copied_label: string;
    steps: Array<{
      title: string;
      description: string;
      command?: string;
    }>;
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
