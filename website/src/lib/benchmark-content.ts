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

export type BenchmarkMetricRow = {
  display: string;
  name: string;
  scale: number;
  value: number;
};

export type BenchmarkContent = {
  cta: {
    href: string;
    label: string;
    lead: string;
    title: string;
  };
  hero: {
    actions: Array<{
      label: string;
      target: string;
    }>;
    actions_label: string;
    label: string;
    lead: string;
    results: Array<{
      accessible: string;
      decimals: number;
      display: string;
      metric: string;
      name: string;
      suffix: string;
      value: number;
    }>;
    title: string[];
    visual_label: string;
  };
  leaderboards: {
    lead: string;
    locomo: {
      columns: Record<'evidence' | 'protocol' | 'rank' | 'score' | 'system', string>;
      count: string;
      lead: string;
      note: string;
      note_title: string;
      rows: Array<{
        evidence: string;
        highlight?: boolean;
        name: string;
        protocol: string;
        rank: number;
        score: string;
        source: string;
      }>;
      tab: string;
      table_label: string;
      title: string;
    };
    source_label: string;
    tabs_label: string;
    swe: {
      columns: Record<'harness' | 'provider' | 'rank' | 'score' | 'system', string>;
      count: string;
      harness_default: string;
      harness_star: string;
      lead: string;
      note: string;
      note_title: string;
      rank_note: string;
      rows: Array<{
        ci: string;
        name: string;
        provider: string;
        rank: number;
        score: string;
        star?: boolean;
      }>;
      source: string;
      spotlight: {
        detail: string;
        label: string;
        status: string;
        value: string;
      };
      tab: string;
      table_label: string;
      title: string;
    };
    title: string;
    updated: string;
  };
  locomo: {
    categories: Array<{
      count: string;
      description: string;
      name: string;
    }>;
    categories_label: string;
    facts: Array<{
      label: string;
      value: string;
    }>;
    lead: string;
    metrics: Array<{
      callout: string;
      callout_detail: string;
      chart_label: string;
      direction: string;
      id: string;
      label: string;
      rows: BenchmarkMetricRow[];
    }>;
    results_lead: string;
    results_title: string;
    scope: string;
    scope_title: string;
    tabs_label: string;
    title: string;
  };
  orientation: {
    lead: string;
    tests: Array<{
      answer: string;
      name: string;
      question: string;
      target: string;
    }>;
    title: string;
  };
  reading: {
    columns: {
      dimension: string;
    };
    lead: string;
    rows: Array<{
      dimension: string;
      locomo: string;
      swe: string;
    }>;
    table_label: string;
    title: string;
  };
  sources: {
    groups: Array<{
      id: string;
      items: Array<{
        href: string;
        label: string;
        type: string;
      }>;
      title: string;
    }>;
    lead: string;
    title: string;
  };
  swe: {
    caption: string;
    delta: string;
    delta_label: string;
    lead: string;
    method: Array<{
      description: string;
      title: string;
    }>;
    scores: Array<{
      accessible: string;
      count: number;
      kind: string;
      label: string;
      rate: string;
    }>;
    scope: string;
    scope_title: string;
    task_count: number;
    title: string;
  };
};

type BenchmarkFrontmatter = {
  benchmark?: BenchmarkContent;
};

export async function getBenchmarkContent(lang: Language): Promise<BenchmarkContent> {
  const sourcePath = path.resolve(process.cwd(), '..', 'docs', lang, 'benchmarks', 'index.md');
  const source = await readFile(sourcePath, 'utf8');
  const end = source.indexOf('\n---', 4);

  if (!source.startsWith('---\n') || end === -1) {
    throw new Error(`Benchmark frontmatter is missing in ${sourcePath}`);
  }

  const frontmatter = parse(source.slice(4, end)) as BenchmarkFrontmatter;
  if (!frontmatter.benchmark) {
    throw new Error(`Benchmark data is missing in ${sourcePath}`);
  }

  return frontmatter.benchmark;
}
