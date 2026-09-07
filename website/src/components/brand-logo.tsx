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

type BrandLogoProps = {
  className?: string;
  priority?: boolean;
};

export function BrandLogo({ className, priority = false }: BrandLogoProps) {
  return (
    <span aria-label="PowerContext" className={['block leading-none', className].filter(Boolean).join(' ')} role="img">
      <img
        alt=""
        className="h-auto w-full dark:hidden"
        decoding="async"
        fetchPriority={priority ? 'high' : 'auto'}
        height={240}
        src="/powercontext-color.png"
        width={1696}
      />
      <img
        alt=""
        className="hidden h-auto w-full dark:block"
        decoding="async"
        fetchPriority={priority ? 'high' : 'auto'}
        height={240}
        src="/powercontext-reverse.png"
        width={1696}
      />
    </span>
  );
}
