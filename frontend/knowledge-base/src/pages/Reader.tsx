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
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Checkbox, Empty, Flex, Select, Space, Spin, Typography } from "@oceanbase/design";
import { PageContainer } from "@oceanbase/ui";
import {
  useChunk,
  useChunks,
  useDocuments,
  useGovernance,
  useHead,
  useRevisions,
  useProcessingRequests,
  trackProcessing,
} from "../hooks";
import { useQueryClient } from "@tanstack/react-query";
import type { Location } from "../types";
import { Failure } from "../components/Feedback";
import { Markdown } from "../components/Markdown";
import * as Actions from "../actions";
export function Reader({
  location,
  libraryTitle,
  onBack,
}: {
  location: Location;
  libraryTitle?: string;
  onBack(): void;
}) {
  const { scope, id } = location;
  const client = useQueryClient();
  const documents = useDocuments(scope);
  const lastRequest = documents.data?.pages[0]?.requests.find((item) => item.document_id === id);
  const pending = useProcessingRequests().data.some((item) => item.scope === scope && item.document === id);
  useEffect(() => {
    for (const request of documents.data?.pages[0]?.requests || []) {
      if (request.document_id === id && ["pending", "processing"].includes(request.state))
        trackProcessing(client, scope, request);
    }
  }, [documents.data, scope, id, client]);
  const [selection, setSelection] = useState<{ revision: number; ids: string[] }>({ revision: 0, ids: [] });
  const head = useHead(scope, id);
  const governance = useGovernance(scope, id);
  const history = useRevisions(scope, id);
  const [chosen, setChosen] = useState<number | undefined>(location.revision);
  const revision = chosen ?? head.data?.revision ?? 0;
  const [selected, setSelected] = useState<string | undefined>(location.chunk);
  const [selectionRevision, setSelectionRevision] = useState(revision);
  const directory = useChunks(scope, id, revision);
  const chunks = directory.data?.pages.flatMap((page) => page.items) || [];
  const selectedId =
    selectionRevision === revision && selected
      ? selected
      : location.revision === revision && location.chunk
        ? location.chunk
        : chunks[0]?.chunk_id || "";
  const body = useChunk(scope, id, revision, selectedId);
  const active = governance.data?.lifecycle_state === "active";
  const editable = !!head.data && active && revision === head.data.revision && !pending;
  const checked = selection.revision === revision ? selection.ids : [];
  const selectChunk = (chunk: string, value: boolean) =>
    setSelection({ revision, ids: value ? [...checked, chunk] : checked.filter((id) => id !== chunk) });
  const versions = [
    ...new Set(
      [
        head.data?.revision,
        revision,
        ...(history.data?.pages.flatMap((page) => page.items.map((item) => item.revision)) || []),
      ].filter((v): v is number => !!v),
    ),
  ].sort((a, b) => b - a);
  const changed = () => {
    setSelection({ revision: 0, ids: [] });
    setChosen(undefined);
    setSelected(undefined);
    setSelectionRevision(0);
  };
  const heading = body.data?.heading_path.join(" / ") || "无标题分块";
  return (
    <PageContainer
      className="kb-page reader-page"
      title={head.data?.content.title || "文档详情"}
      onBack={onBack}
      extra={
        head.data && (
          <Actions.DocumentMenu
            target={{ scope, id, revision: head.data.revision }}
            active={editable}
            modified={head.data.content.manually_modified}
            onChanged={changed}
          />
        )
      }
    >
      <Flex vertical gap="middle">
        <Flex justify="space-between" align="center" wrap gap="small">
          <Typography.Text type="secondary">
            {libraryTitle ? `知识库：${libraryTitle} · ` : ""}
            {head.data?.content.relative_path}
          </Typography.Text>
          <Space>
            <Badge
              status={active ? "success" : "default"}
              text={active ? "可检索" : governance.data ? "已遗忘" : "读取状态中"}
            />
            <Select
              aria-label="文档版本"
              value={revision || undefined}
              style={{ minWidth: 155 }}
              options={versions.map((v) => ({
                value: v,
                label: `v${v}${v === head.data?.revision ? " · 当前版本" : " · 历史版本"}`,
              }))}
              onChange={(value) => {
                setSelection({ revision: value, ids: [] });
                setChosen(value);
                setSelected(undefined);
                setSelectionRevision(value);
              }}
            />
            {history.hasNextPage && (
              <Button size="small" loading={history.isFetchingNextPage} onClick={() => history.fetchNextPage()}>
                更多版本
              </Button>
            )}
          </Space>
        </Flex>
        <Failure error={head.error || governance.error || directory.error || history.error} />
        {pending && (
          <Alert
            type="info"
            showIcon
            message="正在重新解析，完成前继续使用当前版本"
            description="完成后会刷新文档版本、分块目录和检索结果。"
          />
        )}
        {!pending && lastRequest?.state === "failed" && (
          <Alert type="error" showIcon message="最近一次解析失败，当前可用版本保留" description={lastRequest.error} />
        )}
        {head.data && governance.data && (!active || revision !== head.data.revision) && (
          <Alert
            type="info"
            showIcon
            message={active ? "正在查看历史版本，仅可阅读" : "文档已遗忘，不参与普通检索；历史内容仍可查看"}
          />
        )}
        <div className="reader-grid">
          <Card
            className="chunk-directory"
            title={`分块目录${directory.data ? ` · ${directory.data.pages[0].total}` : ""}`}
            size="small"
          >
            <Spin spinning={directory.isPending && revision > 0}>
              <Flex vertical gap="small">
                <Space wrap>
                  <Checkbox
                    aria-label="选择已加载分块"
                    disabled={!editable || !chunks.length || chunks.length > 100}
                    checked={chunks.length > 0 && chunks.every((item) => checked.includes(item.chunk_id))}
                    indeterminate={checked.length > 0 && !chunks.every((item) => checked.includes(item.chunk_id))}
                    onChange={(event: { target: { checked: boolean } }) =>
                      setSelection({ revision, ids: event.target.checked ? chunks.map((item) => item.chunk_id) : [] })
                    }
                  >
                    已加载分块
                  </Checkbox>
                  {checked.length > 0 && (
                    <Button size="small" type="text" onClick={() => setSelection({ revision, ids: [] })}>
                      清空选择
                    </Button>
                  )}
                  <Actions.RemoveChunks
                    key={revision}
                    target={{ scope, id, revision }}
                    ids={checked}
                    disabled={!editable}
                    onRemoved={changed}
                  />
                </Space>
                {location.chunk &&
                  !chunks.some((c) => c.chunk_id === location.chunk) &&
                  location.revision === revision && (
                    <Button
                      block
                      onClick={() => {
                        setSelected(location.chunk);
                        setSelectionRevision(revision);
                      }}
                    >
                      查看检索命中分块
                    </Button>
                  )}
                {chunks.map((item) => (
                  <Flex key={item.chunk_id} align="center" gap="small">
                    <Checkbox
                      aria-label={`选择分块 ${item.ordinal + 1}`}
                      disabled={!editable || (!checked.includes(item.chunk_id) && checked.length >= 100)}
                      checked={checked.includes(item.chunk_id)}
                      onChange={(event: { target: { checked: boolean } }) =>
                        selectChunk(item.chunk_id, event.target.checked)
                      }
                    />
                    <Button
                      type={item.chunk_id === selectedId ? "link" : "text"}
                      block
                      className="chunk-link"
                      aria-pressed={item.chunk_id === selectedId}
                      onClick={() => {
                        setSelected(item.chunk_id);
                        setSelectionRevision(revision);
                      }}
                    >
                      <Flex vertical align="start">
                        <span>
                          {String(item.ordinal + 1).padStart(2, "0")} · {item.heading_path.at(-1) || "无标题分块"}
                        </span>
                        <Typography.Text type="secondary">
                          {item.length} 字符{item.manually_modified ? " · 人工修改" : ""}
                        </Typography.Text>
                      </Flex>
                    </Button>
                  </Flex>
                ))}
                {!directory.isPending && !chunks.length && (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此版本没有分块" />
                )}
                {directory.hasNextPage && (
                  <Button loading={directory.isFetchingNextPage} onClick={() => directory.fetchNextPage()}>
                    加载更多分块
                  </Button>
                )}
              </Flex>
            </Spin>
          </Card>
          <Card
            className="reader-content"
            title={
              body.data ? (
                <Space>
                  <Typography.Text>{heading}</Typography.Text>
                  <Typography.Text type="secondary">#{body.data.ordinal + 1}</Typography.Text>
                </Space>
              ) : (
                "正文预览"
              )
            }
            extra={
              body.data && (
                <Space>
                  <Actions.EditChunk
                    key={`${revision}:${body.data.chunk_id}`}
                    target={{ scope, id, revision }}
                    chunk={body.data}
                    disabled={!editable}
                    onSaved={() => {
                      setChosen(undefined);
                      setSelected(selectedId);
                      setSelectionRevision(revision + 1);
                    }}
                  />
                  <Actions.DocumentMenu
                    target={{ scope, id, revision }}
                    active={editable}
                    modified={head.data?.content.manually_modified || false}
                    chunk={body.data.chunk_id}
                    onChanged={changed}
                  />
                </Space>
              )
            }
          >
            <Failure error={body.error} />
            {body.isFetching && <Spin />}
            {body.data && (
              <Flex vertical gap="middle">
                <Markdown key={`${revision}:${body.data.chunk_id}`} text={body.data.text} />
                <Typography.Text type="secondary">
                  出处：
                  {"line_start" in body.data.locator
                    ? `原文第 ${body.data.locator.line_start}–${body.data.locator.line_end} 行`
                    : `OCR 文字块 ${Number(body.data.locator.ocr_block || 0) + 1}`}{" "}
                  · {body.data.length} 字符{body.data.manually_modified ? " · 已人工修改，文字可能与原文不同" : ""}
                </Typography.Text>
              </Flex>
            )}
            {!body.data && !body.isFetching && !body.error && <Empty description="选择左侧分块开始阅读" />}
          </Card>
        </div>
      </Flex>
    </PageContainer>
  );
}
