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

import type { ReactNode } from 'react';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { apiSource } from '@/lib/openapi-source';
import { baseOptions } from '@/lib/site';

export default function ApiLayout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout tree={apiSource.getPageTree()} {...baseOptions('en')} i18n={false}>
      {children}
    </DocsLayout>
  );
}
