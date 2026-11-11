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

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { ImageResponse } from 'next/og';

export const dynamic = 'force-static';

export async function GET() {
  const logo = await readFile(path.join(process.cwd(), 'assets', 'powercontext-color.png'));
  return new ImageResponse(
    <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
      width: '100%', height: '100%', padding: '80px', background: '#faf9f6', color: '#202020' }}>
      <img src={`data:image/png;base64,${logo.toString('base64')}`} width={636} height={90} alt="PowerContext" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
        <div style={{ fontSize: 64, fontWeight: 700, lineHeight: 1.1 }}>Keep work moving across sessions.</div>
        <div style={{ fontSize: 26, color: '#555' }}>Keep decisions and task progress with the project.</div>
      </div>
      <div style={{ fontSize: 22, color: '#555' }}>powercontext.oceanbase.io</div>
    </div>,
    { width: 1200, height: 630 },
  );
}
