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

'use client';

import { useMemo, useRef, useState, type ComponentProps, type KeyboardEvent } from 'react';
import { BookOpen, CodeXml, FileText, X } from 'lucide-react';
import { useDocsSearch } from 'fumadocs-core/search/client';
import { useI18n } from 'fumadocs-ui/contexts/i18n';
import {
  SearchDialog,
  SearchDialogClose,
  SearchDialogContent,
  SearchDialogFooter,
  SearchDialogHeader,
  SearchDialogIcon,
  SearchDialogInput,
  SearchDialogList,
  SearchDialogListItem,
  SearchDialogOverlay,
  useSearch,
  type SharedProps,
} from 'fumadocs-ui/components/dialog/search';
import { createDocsSearchClient, getSearchCategory, type SearchScope } from '@/lib/docs-search';

const labels = {
  zh: {
    scopes: { all: '全部', docs: '使用文档', development: '项目开发', rfcs: 'RFC' },
    scope: '检索范围',
    search: '搜索文档',
    clear: '清空关键词',
    loading: '正在搜索…',
    error: '搜索暂时不可用，请刷新页面后重试。',
    prompt: '搜索功能名称、操作步骤或 RFC 编号',
    empty: '没有找到相关结果，可以试试更短的关键词。',
    emptyScope: '当前范围内没有匹配结果。',
    expand: '搜索全部文档',
    priority: '使用文档优先',
    relevance: '按相关性排序',
    shown: (count: number) => `已显示 ${count} 篇文档`,
    results: '搜索结果',
    other: '其他文档',
    select: '选择',
    open: '打开',
    close: '关闭',
    language: '中文文档',
  },
  en: {
    scopes: { all: 'All', docs: 'User docs', development: 'Development', rfcs: 'RFCs' },
    scope: 'Search scope',
    search: 'Search documentation',
    clear: 'Clear search',
    loading: 'Searching…',
    error: 'Search is unavailable. Refresh the page to try again.',
    prompt: 'Search for a feature, a task, or an RFC number',
    empty: 'No results found. Try a shorter search term.',
    emptyScope: 'No matching results in this scope.',
    expand: 'Search all documents',
    priority: 'User docs first',
    relevance: 'Sorted by relevance',
    shown: (count: number) => `Showing ${count} documents`,
    results: 'Search results',
    other: 'Other documents',
    select: 'Select',
    open: 'Open',
    close: 'Close',
    language: 'English docs',
  },
};

const scopes: SearchScope[] = ['all', 'docs', 'development', 'rfcs'];
const icons = { docs: BookOpen, development: CodeXml, rfcs: FileText };

// Fumadocs handles Enter and arrow keys on the dialog itself. Keep those
// shortcuts from opening a result when the user is operating a control.
function keepControlKeys(event: KeyboardEvent) {
  if (['Enter', ' ', 'ArrowUp', 'ArrowDown'].includes(event.key)) event.stopPropagation();
}

function SearchResult(props: ComponentProps<typeof SearchDialogListItem>) {
  const { setActive } = useSearch();
  return <SearchDialogListItem
    {...props}
    tabIndex={0}
    onFocus={() => setActive(props.item.id)}
    onKeyDown={(event) => {
      if (event.key === ' ') event.preventDefault();
    }}
    onKeyUp={(event) => {
      if (event.key === ' ') event.currentTarget.click();
    }}
  />;
}

export default function DocsSearchDialog(props: SharedProps) {
  const { locale } = useI18n();
  const lang = locale === 'zh' ? 'zh' : 'en';
  const text = labels[lang];
  const [scope, setScope] = useState<SearchScope>('all');
  const input = useRef<HTMLInputElement>(null);
  const client = useMemo(() => createDocsSearchClient(lang, scope), [lang, scope]);
  const { search, setSearch, query } = useDocsSearch({ client });
  const items = !search.trim() || query.isLoading || query.error || query.data === 'empty' ? null : query.data;
  const pages = items?.filter((item) => item.type === 'page') ?? [];
  const groupStarts = new Map<string, ReturnType<typeof getSearchCategory>>();
  for (const [index, page] of pages.entries()) {
    const category = getSearchCategory(page.url);
    if (index === 0 || category !== getSearchCategory(pages[index - 1].url)) groupStarts.set(page.id, category);
  }

  function searchAll() {
    setScope('all');
    input.current?.focus();
  }

  return (
    <SearchDialog {...props} search={search} onSearchChange={setSearch} isLoading={query.isLoading}>
      <SearchDialogOverlay />
      <SearchDialogContent className="pc-search-dialog flex max-w-2xl flex-col">
        <div className="shrink-0">
          <SearchDialogHeader className="gap-3 px-5 pt-5 pb-4">
            <SearchDialogIcon />
            <SearchDialogInput ref={input} aria-label={text.search} placeholder={text.search} autoComplete="off" />
            {search && (
              <button
                type="button"
                aria-label={text.clear}
                className="rounded-md p-2 text-fd-muted-foreground hover:bg-fd-accent focus-visible:outline-2 focus-visible:outline-fd-primary"
                onKeyDownCapture={keepControlKeys}
                onClick={() => { setSearch(''); input.current?.focus(); }}
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            )}
            <SearchDialogClose onKeyDownCapture={keepControlKeys} />
          </SearchDialogHeader>
          <div role="group" aria-label={text.scope} className="flex flex-wrap gap-1 px-4 pb-3" onKeyDownCapture={keepControlKeys}>
            {scopes.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={scope === value}
                onClick={() => setScope(value)}
                className="min-h-11 rounded-lg px-3 text-sm text-fd-muted-foreground transition-colors hover:bg-fd-accent aria-pressed:bg-fd-foreground aria-pressed:text-fd-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fd-primary sm:min-h-9"
              >
                {text.scopes[value]}
              </button>
            ))}
          </div>
        </div>
        <div className="flex min-h-0 flex-col">
          <div role="status" className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-5 py-3 text-xs text-fd-muted-foreground">
            <span>{query.isLoading ? text.loading : items ? `${text.shown(pages.length)} · ${text.scopes[scope]}` : text.scopes[scope]}</span>
            <span>{scope === 'all' ? text.priority : text.relevance}</span>
          </div>
          {query.error ? (
            <p role="alert" className="px-5 py-10 text-center text-sm text-fd-muted-foreground">{text.error}</p>
          ) : !search.trim() ? (
            <p className="px-5 py-10 text-center text-sm text-fd-muted-foreground">{text.prompt}</p>
          ) : null}
          <SearchDialogList
            className="pc-search-results min-h-0"
            aria-label={text.results}
            items={items}
            Empty={() => (
              <div className="px-5 py-10 text-center text-sm text-fd-muted-foreground">
                <p>{scope === 'all' ? text.empty : text.emptyScope}</p>
                {scope !== 'all' && (
                  <button
                    type="button"
                    onClick={searchAll}
                    onKeyDownCapture={keepControlKeys}
                    className="mt-4 min-h-11 rounded-lg bg-fd-foreground px-4 text-fd-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fd-primary"
                  >
                    {text.expand}
                  </button>
                )}
              </div>
            )}
            Item={({ item, onClick }) => {
              const category = groupStarts.get(item.id);
              const Icon = category ? icons[category] : FileText;
              return (
                <>
                  {groupStarts.has(item.id) && (
                    <div className="flex items-center gap-2 px-3 pt-4 pb-2 text-xs font-medium text-fd-muted-foreground">
                      <Icon className="size-3.5" aria-hidden="true" />
                      {category ? text.scopes[category] : text.other}
                    </div>
                  )}
                  <SearchResult
                    item={item}
                    onClick={onClick}
                    className={item.type === 'page' ? 'mt-2 px-3 py-2.5' : 'pc-search-snippet mx-1 py-2'}
                  />
                </>
              );
            }}
          />
        </div>
        <SearchDialogFooter className="flex shrink-0 items-center justify-between gap-3 px-5 py-3 text-xs text-fd-muted-foreground">
          <span className="flex gap-3" aria-hidden="true">
            <span>↑ ↓ {text.select}</span><span>↵ {text.open}</span><span className="hidden sm:inline">esc {text.close}</span>
          </span>
          <span>{text.language}</span>
        </SearchDialogFooter>
      </SearchDialogContent>
    </SearchDialog>
  );
}
