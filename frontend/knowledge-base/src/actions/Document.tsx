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
import { Alert, Button, Drawer, Dropdown, Flex, Input, Modal, Typography } from "@oceanbase/design";
import { EllipsisOutlined, EditOutlined } from "@oceanbase/icons";
import { useDocumentAction } from "../hooks";
import { Failure } from "../components/Feedback";
import type { Target, Chunk } from "../types";
export function EditChunk({
  target,
  chunk,
  disabled,
  onSaved,
}: {
  target: Target;
  chunk: Chunk;
  disabled: boolean;
  onSaved(): void;
}) {
  const [draft, setDraft] = useState<{ target: Target; text: string }>();
  const mutation = useDocumentAction(draft?.target ?? target);
  return (
    <>
      <Button
        icon={<EditOutlined aria-hidden />}
        disabled={disabled}
        onClick={() => {
          mutation.reset();
          setDraft({ target, text: chunk.text });
        }}
      >
        编辑分块
      </Button>
      <Drawer
        title="编辑分块"
        open={!!draft}
        size="large"
        onClose={() => {
          if (!mutation.isPending) setDraft(undefined);
        }}
        maskClosable={false}
        footer={
          <Flex gap="small">
            <Button
              type="primary"
              loading={mutation.isPending}
              disabled={!draft?.text || draft.text.length > 12000}
              onClick={async () => {
                if (!draft) return;
                try {
                  await mutation.mutateAsync({
                    kind: "edit",
                    edit: { operation: "edit_chunk", chunk_id: chunk.chunk_id, text: draft.text },
                  });
                  setDraft(undefined);
                  onSaved();
                } catch {
                  /* Preserve the draft on conflict. */
                }
              }}
            >
              保存新版本
            </Button>
            <Button disabled={mutation.isPending} onClick={() => setDraft(undefined)}>
              取消
            </Button>
          </Flex>
        }
      >
        <Flex vertical gap="middle">
          <Failure error={mutation.error} />
          <Alert
            type="info"
            showIcon
            message={`基于版本 v${draft?.target.revision ?? target.revision} 编辑，保存后生成新版本`}
            description="原文及历史版本保留。若其他操作已更新文档，请保留草稿并刷新后重新编辑。"
          />
          <Typography.Text type="secondary">{chunk.heading_path.join(" / ") || "无标题分块"}</Typography.Text>
          <Input.TextArea
            aria-label="分块正文"
            value={draft?.text || ""}
            rows={20}
            maxLength={12000}
            showCount
            disabled={mutation.isPending}
            onChange={(event) => setDraft((value) => value && { ...value, text: event.target.value })}
          />
        </Flex>
      </Drawer>
    </>
  );
}
export function DocumentMenu({
  target,
  active,
  modified,
  chunk,
  onChanged,
}: {
  target: Target;
  active: boolean;
  modified: boolean;
  chunk?: string;
  onChanged(): void;
}) {
  const [operation, setOperation] = useState<{
    kind: "forget" | "remove" | "reprocess";
    target: Target;
    key: string;
  }>();
  const mutation = useDocumentAction(operation?.target ?? target);
  const titles = { forget: "遗忘文档", remove: "移除分块", reprocess: "重新解析文档" };
  return (
    <>
      <Dropdown
        trigger={["click"]}
        menu={{
          items: chunk
            ? [{ key: "remove", label: "移除分块", danger: true, disabled: !active }]
            : [
                { key: "reprocess", label: "重新解析", disabled: !active },
                { type: "divider" },
                { key: "forget", label: "遗忘文档", danger: true, disabled: !active },
              ],
          onClick: ({ key }) => {
            mutation.reset();
            setOperation({ kind: key as "forget" | "remove" | "reprocess", target, key: crypto.randomUUID() });
          },
        }}
      >
        <Button aria-label={chunk ? "分块更多操作" : "文档更多操作"} icon={<EllipsisOutlined aria-hidden />}>
          更多
        </Button>
      </Dropdown>
      <Modal
        title={operation ? titles[operation.kind] : ""}
        open={!!operation}
        maskClosable={false}
        confirmLoading={mutation.isPending}
        okText="确认"
        cancelText="取消"
        okButtonProps={{ danger: operation?.kind !== "reprocess" }}
        onCancel={() => {
          if (!mutation.isPending) setOperation(undefined);
        }}
        onOk={async () => {
          if (!operation) return;
          try {
            await mutation.mutateAsync(
              operation.kind === "remove"
                ? { kind: "edit", edit: { operation: "remove_chunk", chunk_id: chunk! } }
                : operation.kind === "forget"
                  ? { kind: "forget" }
                  : { kind: "reprocess", replace: modified, key: operation.key },
            );
            setOperation(undefined);
            onChanged();
          } catch {
            /* Show conflict or processing error without changing the selected document. */
          }
        }}
      >
        <Failure error={mutation.error} />
        <Typography.Paragraph>
          {operation?.kind === "forget"
            ? "遗忘后将不再参与普通检索；原文、分块及历史版本仍保留。首期不支持恢复。"
            : operation?.kind === "remove"
              ? "移除此分块将生成新版本，历史版本仍保留。"
              : modified
                ? "重新解析会替换当前人工修改。确认后按当前切块配置创建新的后台处理请求。"
                : "按当前切块配置创建新的后台处理请求；完成前仍使用当前版本。"}
        </Typography.Paragraph>
      </Modal>
    </>
  );
}
