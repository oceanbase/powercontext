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

import React from 'react';
import type { BlogAuthor } from '@/lib/blog-content';

export function AuthorSocialLinks({ author, className }: { author: BlogAuthor; className?: string }) {
  if (!author.socials.github && !author.socials.x) return null;
  return <div className={className}>
    {(['github', 'x'] as const).map((platform) => {
      const handle = author.socials[platform];
      if (!handle) return null;
      const label = platform === 'github' ? 'GitHub' : 'X';
      return <a key={platform} href={`https://${platform === 'github' ? 'github.com' : 'x.com'}/${handle}`}
        aria-label={`${label}: @${handle}`} target="_blank" rel="noopener noreferrer">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true" focusable="false">
          <path d={platform === 'github' ? 'M12 .75a11.25 11.25 0 0 0-3.558 21.923c.563.104.768-.244.768-.543 0-.267-.01-.974-.015-1.912-3.13.68-3.791-1.508-3.791-1.508-.512-1.3-1.25-1.646-1.25-1.646-1.022-.7.078-.686.078-.686 1.13.08 1.724 1.16 1.724 1.16 1.005 1.724 2.638 1.226 3.28.938.102-.73.393-1.226.714-1.508-2.499-.285-5.126-1.25-5.126-5.564 0-1.23.44-2.232 1.16-3.02-.117-.285-.503-1.43.11-2.979 0 0 .944-.302 3.093 1.154A10.79 10.79 0 0 1 12 6.181c.957.004 1.922.13 2.82.378 2.15-1.456 3.092-1.154 3.092-1.154.614 1.55.228 2.694.112 2.979.721.788 1.158 1.79 1.158 3.02 0 4.325-2.631 5.276-5.138 5.555.404.349.766 1.04.766 2.096 0 1.514-.014 2.736-.014 3.108 0 .3.203.652.774.542A11.252 11.252 0 0 0 12 .75Z' : 'M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.64 7.584H.47l8.6-9.835L0 1.154h7.594l5.243 6.932 6.064-6.933Zm-1.29 19.463h2.039L6.487 3.24H4.3l13.31 17.376Z'} />
        </svg>
        <span>@{handle}</span>
      </a>;
    })}
  </div>;
}
