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

export interface StarSnapshot {
  repository: string;
  count: number;
  updatedAt: number;
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

export function createGitHubStarsLoader(fetcher: typeof fetch = fetch) {
  const snapshots = new Map<string, Promise<StarSnapshot | null>>();
  const previewUrl = process.env.NODE_ENV === 'development'
    ? process.env.NEXT_PUBLIC_GITHUB_STARS_PREVIEW_URL : undefined;

  return function load(url: string): Promise<StarSnapshot | null> {
    const repository = githubRepository(url);
    if (!repository) return Promise.resolve(null);
    const existing = snapshots.get(repository);
    if (existing) return existing;

    // One CDN request per repository/page session, shared across navigation instances.
    const snapshot = (async () => {
      try {
        const response = await fetcher(
          previewUrl || `https://raw.githubusercontent.com/${repository}/website-stats/github-stars.json`,
          { signal: AbortSignal.timeout(5000), credentials: 'omit' },
        );
        if (!response.ok) return null;
        const data = await response.json() as StarSnapshot;
        return data?.repository === repository && Number.isSafeInteger(data.count) && data.count >= 0
          && Number.isFinite(data.updatedAt) && data.updatedAt > 0 && data.updatedAt <= Date.now()
          ? data : null;
      } catch {
        return null;
      }
    })();
    snapshots.set(repository, snapshot);
    return snapshot;
  };
}

export const loadGitHubStars = createGitHubStarsLoader();
