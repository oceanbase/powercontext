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

import Link from 'next/link';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { BrandLogo } from '@/components/brand-logo';

export default function LocaleChooser() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 text-center">
      <BrandLogo className="w-64" priority />
      <h1 className="mt-8 text-sm font-medium text-fd-muted-foreground">Choose a language / 选择语言</h1>
      <div className="mt-6 flex gap-3">
        <Link className={buttonVariants({ variant: 'outline' })} href="/en">English</Link>
        <Link className={buttonVariants({ variant: 'outline' })} href="/zh">简体中文</Link>
      </div>
    </main>
  );
}
