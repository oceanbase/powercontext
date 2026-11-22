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
import { Button, Modal, Typography } from "@oceanbase/design";
import { useDocumentAction } from "../hooks";
import { Failure } from "../components/Feedback";
import type { Target } from "../types";
export function RemoveChunks({
  target,
  ids,
  disabled,
  onRemoved,
}: {
  target: Target;
  ids: string[];
  disabled: boolean;
  onRemoved(): void;
}) {
  const [operation, setOperation] = useState<{ target: Target; ids: string[] }>();
  const mutation = useDocumentAction(operation?.target ?? target);
  return (
    <>
      <Button
        size="small"
        danger
        disabled={disabled || !ids.length}
        onClick={() => {
          mutation.reset();
          setOperation({ target: { ...target }, ids: [...ids] });
        }}
      >
        移除选中分块{ids.length ? ` (${ids.length})` : ""}
      </Button>
      <Modal
        title="批量移除分块"
        open={!!operation}
        maskClosable={false}
        okText="确认移除"
        cancelText="取消"
        confirmLoading={mutation.isPending}
        okButtonProps={{ danger: true }}
        onCancel={() => {
          if (!mutation.isPending) setOperation(undefined);
        }}
        onOk={async () => {
          if (!operation) return;
          try {
            await mutation.mutateAsync({
              kind: "edit",
              edit: { operation: "remove_chunks", chunk_ids: operation.ids },
            });
            setOperation(undefined);
            onRemoved();
          } catch {
            /* Keep the exact selection and revision on conflict. */
          }
        }}
      >
        <Failure error={mutation.error} />
        <Typography.Paragraph>
          从 v{operation?.target.revision} 移除 {operation?.ids.length}{" "}
          个分块，只生成一个新版本；历史版本保留。若文档已变化，整批操作将被拒绝。
        </Typography.Paragraph>
      </Modal>
    </>
  );
}
