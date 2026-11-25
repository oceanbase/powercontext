/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
import { Button, Card, Space, Typography } from "@oceanbase/design";
import { useHead } from "../hooks";
import { Markdown } from "./Markdown";
import type { Hit, Location } from "../types";
export function SearchHit({ hit, library, onOpen }: { hit: Hit; library?: string; onOpen(location: Location): void }) {
  const document = useHead(hit.scope_id, hit.document.artifact_id);
  return (
    <Card
      title={
        <Space>
          <Typography.Text>{document.data?.content.title || hit.heading_path.at(-1) || "文档"}</Typography.Text>
          <Typography.Text type="secondary">
            {library} · v{hit.document.revision}
          </Typography.Text>
        </Space>
      }
      extra={
        <Button
          size="small"
          onClick={() =>
            onOpen({
              scope: hit.scope_id,
              id: hit.document.artifact_id,
              revision: hit.document.revision,
              chunk: hit.chunk_id,
            })
          }
        >
          定位分块
        </Button>
      }
    >
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          {document.data?.content.relative_path} {hit.heading_path.length > 0 && ` / ${hit.heading_path.join(" / ")}`}
          {hit.manually_modified ? " · 人工修改" : ""}
        </Typography.Text>
        <Markdown controls={false} text={hit.text} />
      </Space>
    </Card>
  );
}
