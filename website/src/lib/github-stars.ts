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

export const starRefreshInterval = 5 * 60 * 1000;

export interface StarSnapshot {
  count: number;
  updatedAt: number;
}

interface CacheEntry {
  snapshot: StarSnapshot | null;
  nextRequestAt: number;
  pending?: Promise<StarSnapshot | null>;
}

export function githubRepository(url: string): string | null {
  try {
    const parsed = new URL(url);
    const match = /^\/([\w.-]+)\/([\w.-]+)\/?$/.exec(parsed.pathname);
    if (parsed.protocol !== 'https:' || parsed.host !== 'github.com' || !match) return null;
    return `${match[1]}/${match[2].replace(/\.git$/, '')}`.toLowerCase();
  } catch {
    return null;
  }
}

function isSnapshot(value: unknown): value is StarSnapshot {
  if (!value || typeof value !== 'object') return false;
  const data = value as StarSnapshot;
  return Number.isSafeInteger(data.count) && data.count >= 0
    && Number.isFinite(data.updatedAt) && data.updatedAt > 0;
}

export function createGitHubStarsClient({
  fetcher = fetch,
  storage = () => window.sessionStorage,
  now = Date.now,
  timeout = 5000,
}: {
  fetcher?: typeof fetch;
  storage?: () => Pick<Storage, 'getItem' | 'setItem'>;
  now?: () => number;
  timeout?: number;
} = {}) {
  const entries = new Map<string, CacheEntry>();
  const cacheKey = (repo: string) => `powercontext:github-stars:${repo}`;

  function entryFor(repo: string): CacheEntry {
    let entry = entries.get(repo);
    if (entry) return entry;
    let snapshot: StarSnapshot | null = null;
    try {
      const saved: unknown = JSON.parse(storage().getItem(cacheKey(repo)) ?? 'null');
      if (isSnapshot(saved) && saved.updatedAt <= now()) snapshot = saved;
    } catch {
      // Storage can be unavailable in privacy modes; the in-memory cache still works.
    }
    entry = { snapshot, nextRequestAt: snapshot ? snapshot.updatedAt + starRefreshInterval : 0 };
    entries.set(repo, entry);
    return entry;
  }

  function peek(url: string): StarSnapshot | null {
    const repo = githubRepository(url);
    return repo ? entryFor(repo).snapshot : null;
  }

  async function load(url: string): Promise<StarSnapshot | null> {
    const repo = githubRepository(url);
    if (!repo) return null;
    const entry = entryFor(repo);
    if (entry.pending) return entry.pending;
    if (now() < entry.nextRequestAt) return entry.snapshot;

    // Share one request across desktop/mobile navigation and throttle failed attempts too.
    entry.nextRequestAt = now() + starRefreshInterval;
    entry.pending = (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      try {
        const response = await fetcher(`https://api.github.com/repos/${repo}`, {
          headers: { Accept: 'application/vnd.github+json' },
          signal: controller.signal,
          credentials: 'omit',
          cache: 'no-store',
        });
        if (!response.ok) return entry.snapshot;
        const data: unknown = await response.json();
        const count = data && typeof data === 'object' && 'stargazers_count' in data
          ? data.stargazers_count : null;
        if (typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0) return entry.snapshot;
        entry.snapshot = { count, updatedAt: now() };
        try {
          storage().setItem(cacheKey(repo), JSON.stringify(entry.snapshot));
        } catch {
          // A storage failure must not discard a successful response.
        }
        return entry.snapshot;
      } catch {
        return entry.snapshot;
      } finally {
        clearTimeout(timer);
      }
    })();
    try {
      return await entry.pending;
    } finally {
      entry.pending = undefined;
    }
  }

  return { peek, load };
}

export const githubStars = createGitHubStarsClient();
