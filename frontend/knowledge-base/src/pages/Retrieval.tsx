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
import { useState } from "react";
import { Alert, Button, Card, Empty, Flex, Input, Select, Typography } from "@oceanbase/design";
import { SearchOutlined } from "@oceanbase/icons";
import { useDocumentChanges, useRetrieve } from "../hooks";
import type { KB, Location } from "../types";
import { SearchHit } from "../components/SearchHit";
import { Failure } from "../components/Feedback";
export function Retrieval({
  space,
  libraries,
  initialScope,
  onOpen,
}: {
  space: string;
  libraries: KB[];
  initialScope?: string;
  onOpen(location: Location): void;
}) {
  const [scopes, setScopes] = useState<string[]>(initialScope ? [initialScope] : libraries.map((k) => k.scope_id));
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [submitted, setSubmitted] = useState("");
  const [generation, setGeneration] = useState(0);
  const mutation = useRetrieve();
  const changes = useDocumentChanges(scopes);
  const signature = JSON.stringify({ space, scopes, query, mode, generation, changes });
  const hits = submitted === signature ? mutation.data?.hits : undefined;
  const invalidate = () => {
    setGeneration((value) => value + 1);
    mutation.reset();
  };
  return (
    <Flex vertical gap="middle">
      <Card title="检索测试">
        <Flex vertical gap="middle">
          <Typography.Text type="secondary">
            选择当前空间内的知识库，检查哪些分块能回答你的问题。仅展示检索结果，不生成回答。
          </Typography.Text>
          <Select
            aria-label="检索知识库"
            mode="multiple"
            value={scopes}
            onChange={(value) => {
              setScopes(value);
              invalidate();
            }}
            placeholder="选择知识库"
            options={libraries.map((k) => ({ value: k.scope_id, label: k.value.title }))}
          />
          <Flex gap="small">
            <Input
              aria-label="检索问题"
              placeholder="例如：重启数据库前需要检查什么？"
              maxLength={2000}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                invalidate();
              }}
              onPressEnter={() => {
                if (query.trim() && scopes.length && scopes.length <= 20 && !mutation.isPending) {
                  setSubmitted(signature);
                  mutation.mutate({ space, scopes, query, mode });
                }
              }}
            />
            <Select
              aria-label="检索方式"
              value={mode}
              style={{ minWidth: 130 }}
              onChange={(value) => {
                setMode(value);
                invalidate();
              }}
              options={[
                { value: "hybrid", label: "混合检索" },
                { value: "fts", label: "全文检索" },
                { value: "vector", label: "向量检索" },
              ]}
            />
            <Button
              type="primary"
              icon={<SearchOutlined aria-hidden />}
              loading={mutation.isPending}
              disabled={!query.trim() || !scopes.length || scopes.length > 20}
              onClick={() => {
                setSubmitted(signature);
                mutation.mutate({ space, scopes, query, mode });
              }}
            >
              检索
            </Button>
          </Flex>
          <Typography.Text type="secondary">最多选择 20 个知识库；未选择时不会搜索其他知识库。</Typography.Text>
        </Flex>
      </Card>
      <Failure error={submitted === signature ? mutation.error : null} />
      {submitted && submitted !== signature && (
        <Alert type="info" showIcon message="内容或检索条件已变化，请重新检索。" />
      )}
      {hits && <Typography.Text type="secondary">找到 {hits.length} 个相关分块</Typography.Text>}
      {hits?.map((hit) => (
        <SearchHit
          key={`${hit.scope_id}:${hit.document.artifact_id}:${hit.document.revision}:${hit.chunk_id}`}
          hit={hit}
          library={libraries.find((k) => k.scope_id === hit.scope_id)?.value.title}
          onOpen={onOpen}
        />
      ))}
      {hits?.length === 0 && <Empty description="没有匹配结果，请调整关键词或知识库范围" />}
      {!submitted && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="输入问题，验证知识库的召回效果" />}
    </Flex>
  );
}
