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

const counts = new Map<string, Promise<string | null>>();

export function loadGitHubStars(url: string): Promise<string | null> {
  const repository = githubRepository(url);
  if (!repository) return Promise.resolve(null);
  const existing = counts.get(repository);
  if (existing) return existing;

  // Share one request per repository/page session, including failures, without polling.
  const count = (async () => {
    try {
      const response = await fetch(`https://img.shields.io/github/stars/${repository}.json`, {
        signal: AbortSignal.timeout(5000), credentials: 'omit',
      });
      if (!response.ok) return null;
      const data = await response.json();
      // Shields returns display text such as "1.2k", not an exact count.
      return data?.label === 'stars' && !data.isError && typeof data.message === 'string'
        && /^(?:\d+|\d+(?:\.\d+)?[kMGTPEZY])$/.test(data.message) ? data.message : null;
    } catch {
      return null;
    }
  })();
  counts.set(repository, count);
  return count;
}
