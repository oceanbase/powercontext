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

import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Language } from "./messages";

const copy = {
  zh: {
    body: "请先安装或连接 PowerContext 服务",
    deploy: "安装服务",
    connect: "连接服务",
  },
  en: {
    body: "Install or connect a PowerContext service first",
    deploy: "Install service",
    connect: "Connect service",
  },
} as const;

export function ConnectionRequiredGuide({
  title,
  language,
  onDeploy,
  onConnect,
}: {
  title: string;
  language: Language;
  onDeploy?: () => void;
  onConnect: () => void;
}) {
  const t = copy[language];

  return (
    <section
      className="memory-empty-state"
      aria-labelledby="connection-required-title"
    >
      <span className="memory-empty-icon" aria-hidden="true">
        <Search />
      </span>
      <h2 id="connection-required-title">{title}</h2>
      <p>{t.body}</p>
      <div className="memory-empty-actions">
        {onDeploy && (
          <Button size="sm" onClick={onDeploy}>
            {t.deploy}
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={onConnect}>
          {t.connect}
        </Button>
      </div>
    </section>
  );
}
