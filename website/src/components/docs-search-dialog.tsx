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
import { useDocsSearch } from 'fumadocs-core/search/client';
import { useI18n } from 'fumadocs-ui/contexts/i18n';
import {
  SearchDialog,
  SearchDialogContent,
  SearchDialogHeader,
  SearchDialogIcon,
  SearchDialogInput,
  SearchDialogList,
  SearchDialogListItem,
  SearchDialogOverlay,
  useSearch,
  type SharedProps,
} from 'fumadocs-ui/components/dialog/search';
import { buttonClasses, Kbd, Segmented } from '@astrafoundry/design/ui';
import { createDocsSearchClient, getSearchCategory, type SearchScope } from '@/lib/docs-search';

// The dialog says only what the reader cannot already see: a placeholder,
// the scope, the results, and what to do when nothing matches.
const labels = {
  zh: {
    scopes: { all: '全部', docs: '文档', development: '开发', rfcs: 'RFC' },
    scope: '检索范围',
    search: '搜索文档',
    close: '关闭搜索',
    error: '搜索暂时不可用，请刷新页面后重试。',
    empty: '没有找到结果',
    expand: '搜索全部',
    results: '搜索结果',
    other: '其他',
  },
  en: {
    scopes: { all: 'All', docs: 'Docs', development: 'Development', rfcs: 'RFCs' },
    scope: 'Search scope',
    search: 'Search documentation',
    close: 'Close search',
    error: 'Search is unavailable. Refresh the page to try again.',
    empty: 'No results',
    expand: 'Search all',
    results: 'Search results',
    other: 'Other',
  },
};

const scopes: SearchScope[] = ['all', 'docs', 'development', 'rfcs'];

// Fumadocs handles Enter and arrow keys on the dialog itself. Keep those
// shortcuts from opening a result when the user is operating a control.
function keepControlKeys(event: KeyboardEvent) {
  if (['Enter', ' ', 'ArrowUp', 'ArrowDown'].includes(event.key)) event.stopPropagation();
}

// The scope picker is a radio group that moves its choice with every arrow
// key, so let it handle the key first and stop it only on the way up.
function keepScopeKeys(event: KeyboardEvent) {
  if (['Enter', ' ', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.stopPropagation();
  }
}

function SearchResult(props: ComponentProps<typeof SearchDialogListItem>) {
  const { setActive } = useSearch();
  // The first two levels are the content root and the section, which the
  // group heading above the results already names.
  const item = 'breadcrumbs' in props.item ? { ...props.item, breadcrumbs: props.item.breadcrumbs?.slice(2) } : props.item;
  return <SearchDialogListItem
    {...props}
    item={item}
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

/** Closes the dialog; the key it shows is the one that also closes it. */
function CloseSearch({ label }: { label: string }) {
  const { onOpenChange } = useSearch();
  return (
    <button
      aria-label={label}
      className="rounded-md transition-transform active:scale-[0.97] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      onClick={() => onOpenChange(false)}
      onKeyDownCapture={keepControlKeys}
      type="button"
    >
      <Kbd>Esc</Kbd>
    </button>
  );
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
      <SearchDialogOverlay className="backdrop-blur-none" />
      <SearchDialogContent className="pc-search-dialog flex max-w-2xl flex-col" data-material="raised">
        <div className="shrink-0">
          <SearchDialogHeader className="gap-3 px-5 pt-5 pb-4">
            <SearchDialogIcon />
            <SearchDialogInput ref={input} aria-label={text.search} placeholder={text.search} autoComplete="off" />
            <CloseSearch label={text.close} />
          </SearchDialogHeader>
          <div className="px-4 pb-3">
            <div className="contents" onKeyDown={keepScopeKeys}>
              <Segmented
                label={text.scope}
                onValueChange={setScope}
                options={scopes.map((value) => ({ value, label: text.scopes[value] }))}
                value={scope}
              />
            </div>
          </div>
        </div>
        <div className="flex min-h-0 flex-col">
          {query.error ? (
            <p role="alert" className="px-5 py-10 text-center text-sm text-fd-muted-foreground">{text.error}</p>
          ) : null}
          <SearchDialogList
            className="pc-search-results min-h-0"
            aria-label={text.results}
            items={items}
            Empty={() => (
              <div className="px-5 py-10 text-center text-sm text-fd-muted-foreground">
                <p>{text.empty}</p>
                {scope !== 'all' && (
                  <button type="button" onClick={searchAll} onKeyDownCapture={keepControlKeys} className={buttonClasses('secondary', 'sm', 'mt-4')}>
                    {text.expand}
                  </button>
                )}
              </div>
            )}
            Item={({ item, onClick }) => {
              const category = groupStarts.get(item.id);
              return (
                <>
                  {scope === 'all' && groupStarts.has(item.id) && (
                    <div className="px-3 pt-4 pb-1 text-xs font-medium text-fd-muted-foreground">
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
      </SearchDialogContent>
    </SearchDialog>
  );
}
