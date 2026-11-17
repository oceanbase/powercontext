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

import type { Language } from '@/lib/i18n';
import { withBasePath } from '@/lib/urls';

/**
 * A post about PowerContext on X, quoted as written. `name` and `avatar` are
 * the author's X profile as read when the quote was added; the avatar is
 * stored under public/testimonials. A locale whose language differs from the
 * post's shows the translation.
 */
type TestimonialSource = {
  handle: string;
  name: string;
  avatar: string;
  lang: Language;
  text: string;
  translation: string;
};

export type Testimonial = {
  handle: string;
  name: string;
  avatar: string;
  /** The author's X profile. */
  href: string;
  /** Language of `text` as shown. */
  lang: Language;
  text: string;
};

const sources: TestimonialSource[] = [
  {
    handle: 'pluoki',
    name: 'pluoki',
    avatar: 'x-pluoki.webp',
    lang: 'en',
    text: 'PowerContext can automatically extract memory and revise topic summaries. Its formal Experience workflow adds a review gate before reusable lessons become available for recall.',
    translation: 'PowerContext 可以自动提取记忆、修订主题摘要。它正式的 Experience 流程加了一道审核关卡，可复用的经验通过之后才会进入召回。',
  },
  {
    handle: 'Musfengtang',
    name: 'David 的生意实验室',
    avatar: 'x-musfengtang.webp',
    lang: 'zh',
    text: '昨天在 Claude Code 里排过的部署问题，今天换 Codex 还能接着处理。这个 PowerContext 插件的念头很对胃口：换个工具，别又从自我介绍开始。',
    translation: 'A deployment problem I was debugging in Claude Code yesterday, I could pick up again in Codex today. The idea behind this PowerContext plugin is just to my taste: switch tools without starting over from introductions.',
  },
  {
    handle: 'BIGBULLapp',
    name: 'Big Bull',
    avatar: 'x-bigbullapp.webp',
    lang: 'en',
    text: 'PowerContext moves the durable parts out of the conversation and into a context service, so decisions, constraints, evidence and progress survive the session that produced them.',
    translation: 'PowerContext 把需要长期保留的部分从对话里搬到一个上下文服务中，决定、约束、依据和进展因此不会随产生它们的会话一起消失。',
  },
  {
    handle: 'ScottSiu1983',
    name: 'Scott Siu 萧少聪',
    avatar: 'x-scottsiu1983.webp',
    lang: 'zh',
    text: 'PowerContext v1.5.0 的上下文回执是个挺实在的特性。以前 Agent 答错了只能猜：没找到、找到没给，还是给了没用？有了回执，至少前两个能查清楚。',
    translation: 'The context receipt in PowerContext v1.5.0 is a genuinely practical feature. When an Agent got something wrong, you used to have to guess: was it never found, found but not passed on, or passed on and ignored? With receipts, at least the first two can be checked.',
  },
];

/** The posts as the `lang` page shows them. */
export function testimonialsFor(lang: Language): Testimonial[] {
  return sources.map((source) => {
    const original = source.lang === lang;
    return {
      handle: source.handle,
      name: source.name,
      avatar: withBasePath(`/testimonials/${source.avatar}`),
      href: `https://x.com/${source.handle}`,
      lang,
      text: original ? source.text : source.translation,
    };
  });
}
