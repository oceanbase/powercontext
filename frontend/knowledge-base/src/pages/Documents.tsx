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
import { Badge, Button, Card, Empty, Flex, Input, Select, Space, Table, Tooltip, Typography } from "@oceanbase/design";
import { FileTextOutlined, PictureOutlined, ReloadOutlined, SearchOutlined } from "@oceanbase/icons";
import { useDocuments } from "../hooks";
import type { Document, KB, Location, Processing } from "../types";
import { isForbidden } from "../services";
import { Failure } from "../components/Feedback";
import * as Actions from "../actions";
const statusNames: Record<string, string> = {
  pending: "等待解析",
  processing: "解析中",
  failed: "解析失败",
  completed: "已完成",
};
export function Documents({ kb, onOpen }: { kb: KB; onOpen(location: Location): void }) {
  const query = useDocuments(kb.scope_id);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [records, setRecords] = useState(false);
  const [selected, setSelected] = useState<Document[]>([]);
  if (isForbidden(query.error)) return <Failure error={query.error} />;
  const rows = query.data?.pages.flatMap((page) => page.items) || [];
  const requests = query.data?.pages[0]?.requests || [];
  const latest = new Map<string, Processing>();
  for (const request of requests) if (!latest.has(request.document_id)) latest.set(request.document_id, request);
  const names = new Map(rows.map((doc) => [doc.ref.artifact_id, doc.content.relative_path]));
  const visible = rows.filter(
    (doc) =>
      doc.content.relative_path.toLowerCase().includes(search.toLowerCase()) &&
      (status === "all" ||
        (status === "failed" ? latest.get(doc.ref.artifact_id)?.state === "failed" : doc.lifecycle_state === status)),
  );
  const checked = selected.filter((doc) =>
    visible.some((row) =>
      row.ref.artifact_id === doc.ref.artifact_id &&
      row.ref.revision === doc.ref.revision &&
      row.lifecycle_state === "active",
    ),
  );
  const open = (doc: Document) => onOpen({ scope: kb.scope_id, id: doc.ref.artifact_id });
  return (
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="center" gap="middle" wrap>
        <Space>
          <Input
            aria-label="筛选文档"
            prefix={<SearchOutlined aria-hidden />}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setSelected([]);
            }}
            allowClear
            placeholder="筛选已加载文档"
          />
          <Select
            aria-label="文档状态"
            value={status}
            onChange={(value) => {
              setStatus(value);
              setSelected([]);
            }}
            style={{ minWidth: 140 }}
            options={[
              { value: "all", label: "全部状态" },
              { value: "active", label: "可检索" },
              { value: "deprecated", label: "已遗忘" },
              { value: "failed", label: "最近解析失败" },
            ]}
          />
        </Space>
        <Space>
          <Button onClick={() => setRecords(!records)}>
            处理记录{requests.some((r) => r.state === "failed") ? " · 有失败" : ""}
          </Button>
          <Actions.Import scope={kb.scope_id} />
          <Button
            aria-label="刷新文档"
            icon={<ReloadOutlined aria-hidden />}
            loading={query.isFetching}
            onClick={() => query.refetch()}
          />
        </Space>
      </Flex>
      <Flex justify="space-between" align="center" gap="middle" wrap>
        <Space wrap>
          <Typography.Text>已选择 {checked.length} 篇文档</Typography.Text>
          <Typography.Text type="secondary">全选仅包含当前筛选后已加载的可检索文档</Typography.Text>
          {checked.length > 0 && <Button size="small" onClick={() => setSelected([])}>清空选择</Button>}
        </Space>
        <Actions.BatchDocuments scope={kb.scope_id} documents={checked} onCompleted={() => setSelected([])} />
      </Flex>
      <Failure error={query.error} />
      <Card styles={{ body: { padding: 0 } }}>
        <Table<Document>
          rowKey={(row) => row.ref.artifact_id}
          size="middle"
          rowSelection={{
            fixed: true,
            columnWidth: 48,
            selectedRowKeys: checked.map((doc) => doc.ref.artifact_id),
            onChange: (_, documents) => setSelected(documents),
            getCheckboxProps: (doc) => ({
              disabled: doc.lifecycle_state !== "active",
              "aria-label": `选择文档 ${doc.content.relative_path}`,
            }),
          }}
          dataSource={visible}
          loading={query.isPending}
          pagination={false}
          locale={{
            emptyText: (
              <Empty description={rows.length ? "没有匹配的文档" : "还没有已发布文档，导入后可在处理记录中查看状态"} />
            ),
          }}
          scroll={{ x: 720 }}
          columns={[
            {
              title: "文档名称",
              key: "name",
              fixed: "left",
              render: (_, doc) => (
                <Flex align="start" gap="small">
                  {/\.(md|markdown)$/i.test(doc.content.relative_path) ? (
                    <FileTextOutlined aria-hidden />
                  ) : (
                    <PictureOutlined aria-hidden />
                  )}
                  <Flex vertical className="document-name">
                    <Tooltip title={doc.content.title} trigger={["hover", "focus"]}>
                      <Typography.Link
                        role="button"
                        tabIndex={0}
                        aria-label={doc.content.title}
                        onClick={() => open(doc)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            open(doc);
                          }
                        }}
                      >
                        <span className="document-title">{doc.content.title}</span>
                      </Typography.Link>
                    </Tooltip>
                    {doc.content.relative_path !== doc.content.title && (
                      <Typography.Text type="secondary" ellipsis={{ tooltip: doc.content.relative_path }} className="file-path">
                        {doc.content.relative_path}
                      </Typography.Text>
                    )}
                  </Flex>
                </Flex>
              ),
            },
            {
              title: "状态",
              width: 175,
              render: (_, doc) => (
                <Flex vertical gap="small">
                  <Badge
                    status={doc.lifecycle_state === "active" ? "success" : "default"}
                    text={doc.lifecycle_state === "active" ? "可检索" : "已遗忘"}
                  />
                  {latest.get(doc.ref.artifact_id)?.state === "failed" && (
                    <Typography.Text type="danger">重新解析失败 · 旧版本保留</Typography.Text>
                  )}
                  {["pending", "processing"].includes(latest.get(doc.ref.artifact_id)?.state || "") && (
                    <Typography.Text type="secondary">
                      {statusNames[latest.get(doc.ref.artifact_id)!.state]}
                    </Typography.Text>
                  )}
                </Flex>
              ),
            },
            { title: "可用版本", width: 90, render: (_, doc) => `v${doc.ref.revision}` },
            { title: "分块数", width: 85, render: (_, doc) => doc.content.chunk_count },
            {
              title: "操作",
              width: 85,
              fixed: "right",
              render: (_, doc) => (
                <Button size="small" onClick={() => open(doc)}>
                  浏览
                </Button>
              ),
            },
          ]}
        />
      </Card>
      <Flex justify="space-between" align="center">
        <Typography.Text type="secondary">
          已加载 {rows.length} 篇文档{search || status !== "all" ? `，匹配 ${visible.length} 篇` : ""}
        </Typography.Text>
        {query.hasNextPage && (
          <Button loading={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
            加载更多文档
          </Button>
        )}
      </Flex>
      {records && (
        <Card title="近期处理记录" extra={<Typography.Text type="secondary">最近 50 条请求</Typography.Text>}>
          <Table<Processing>
            size="small"
            rowKey="request_id"
            dataSource={requests}
            pagination={{ pageSize: 10, hideOnSinglePage: true }}
            columns={[
              {
                title: "文档",
                render: (_, row) =>
                  row.relative_path || names.get(row.document_id) || `未发布文档 · ${row.document_id.slice(0, 10)}`,
              },
              {
                title: "处理状态",
                render: (_, row) => (
                  <Badge
                    status={row.state === "failed" ? "error" : row.state === "completed" ? "success" : "processing"}
                    text={statusNames[row.state] || row.state}
                  />
                ),
              },
              { title: "说明", dataIndex: "error", render: (value) => value || "—" },
              {
                title: "操作",
                render: (_, row) =>
                  row.state === "failed" ? <Actions.RetryProcessing scope={kb.scope_id} request={row} /> : "—",
              },
            ]}
          />
        </Card>
      )}
    </Flex>
  );
}
