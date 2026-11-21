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
import { useRef, useState } from "react";
import { Alert, Button, Checkbox, Flex, Modal, Space, Table, Typography } from "@oceanbase/design";
import { useBatchDocuments, type BatchDocument } from "../hooks";
import { Failure } from "../components/Feedback";
import type { Document } from "../types";

export function BatchDocuments({ scope, documents, onCompleted }: {
  scope: string;
  documents: Document[];
  onCompleted(): void;
}) {
  const [operation, setOperation] = useState<{ kind: "forget" | "reprocess"; items: BatchDocument[] }>();
  const [replace, setReplace] = useState(false);
  const started = useRef(false);
  const mutation = useBatchDocuments();
  const open = (kind: "forget" | "reprocess") => {
    mutation.reset();
    started.current = false;
    setReplace(false);
    setOperation({ kind, items: documents.map((doc) => ({
      target: { scope, id: doc.ref.artifact_id, revision: doc.ref.revision },
      title: doc.content.relative_path,
      modified: doc.content.manually_modified,
      key: crypto.randomUUID(),
    })) });
  };
  const close = () => {
    if (!mutation.isPending) setOperation(undefined);
  };
  const failed = mutation.data?.filter((item) => item.error).length ?? 0;
  return (
    <>
      <Space wrap>
        <Button disabled={!documents.length || mutation.isPending} onClick={() => open("reprocess")}>批量重新解析</Button>
        <Button danger disabled={!documents.length || mutation.isPending} onClick={() => open("forget")}>批量遗忘</Button>
      </Space>
      <Modal
        title={operation?.kind === "forget" ? "批量遗忘文档" : "批量重新解析文档"}
        open={!!operation}
        maskClosable={false}
        onCancel={close}
        confirmLoading={mutation.isPending}
        okText="确认执行"
        cancelText="取消"
        okButtonProps={{ danger: operation?.kind === "forget", disabled: started.current }}
        cancelButtonProps={{ disabled: mutation.isPending }}
        footer={mutation.data ? <Button onClick={close}>关闭</Button> : undefined}
        onOk={async () => {
          if (!operation || started.current) return;
          started.current = true;
          try {
            await mutation.mutateAsync({ ...operation, replace });
            onCompleted();
          } catch {
            // Do not silently resubmit a batch after an indeterminate outcome.
          }
        }}
      >
        <Flex vertical gap="middle">
          <Failure error={mutation.error} />
          {mutation.data ? (
            <>
              <Alert
                type={failed ? "warning" : "success"}
                showIcon
                message={`成功 ${mutation.data.length - failed} 篇，失败 ${failed} 篇`}
                description={operation?.kind === "reprocess"
                  ? "已提交不代表解析完成，请在处理记录中查看最终结果。失败项请刷新文档状态后重新选择。"
                  : "已遗忘的文档不再参与检索。失败项请刷新文档状态后重新选择。"}
              />
              <Table
                rowKey="id"
                size="small"
                dataSource={mutation.data}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                columns={[
                  { title: "文档", dataIndex: "title", ellipsis: true },
                  { title: "结果", dataIndex: "status" },
                  { title: "原因", dataIndex: "error", render: (value) => value || "—" },
                ]}
              />
            </>
          ) : (
            <>
              <Typography.Paragraph>将对选中的 {operation?.items.length ?? 0} 篇文档逐篇执行，单篇失败不影响其他文档。</Typography.Paragraph>
              <Alert
                type={operation?.kind === "forget" ? "warning" : "info"}
                showIcon
                message={operation?.kind === "forget"
                  ? "遗忘后停止参与检索，原文和历史版本保留，首期不支持恢复。"
                  : "按当前知识库配置重新解析原文快照，完成前继续使用当前版本。默认拒绝替换人工修改。"}
              />
              {operation?.kind === "reprocess" && (
                <Checkbox checked={replace} disabled={mutation.isPending} onChange={(event: { target: { checked: boolean } }) => setReplace(event.target.checked)}>
                  允许覆盖所选文档的人工修改
                </Checkbox>
              )}
              <Table
                rowKey={(item: BatchDocument) => item.target.id}
                size="small"
                dataSource={operation?.items}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                columns={[
                  { title: "文档", dataIndex: "title", ellipsis: true },
                  { title: "选中版本", render: (_, item) => `v${item.target.revision}` },
                  { title: "人工修改", render: (_, item) => item.modified ? "有" : "无" },
                ]}
              />
            </>
          )}
        </Flex>
      </Modal>
    </>
  );
}
