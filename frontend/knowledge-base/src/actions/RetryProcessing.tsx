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
import { Alert, Button, Checkbox, Flex, Modal, Typography } from "@oceanbase/design";
import { useRetry } from "../hooks";
import { Failure } from "../components/Feedback";
import type { Processing } from "../types";

export function RetryProcessing({ scope, request }: { scope: string; request: Processing }) {
  const [intent, setIntent] = useState<{ key: string; replace: boolean }>();
  const mutation = useRetry(scope);
  const close = () => {
    if (!mutation.isPending) {
      setIntent(undefined);
      mutation.reset();
    }
  };
  return (
    <>
      <Button
        size="small"
        onClick={() => {
          mutation.reset();
          setIntent({ key: crypto.randomUUID(), replace: false });
        }}
      >
        重新提交解析
      </Button>
      <Modal
        title="重新提交解析"
        open={!!intent}
        onCancel={close}
        maskClosable={false}
        closable={!mutation.isPending}
        confirmLoading={mutation.isPending}
        okText="确认重新解析"
        cancelText="取消"
        onOk={async () => {
          if (!intent) return;
          try {
            await mutation.mutateAsync({ request: request.request_id, ...intent });
            setIntent(undefined);
          } catch {
            // Preserve the request key and explicit policy for an uncertain outcome.
          }
        }}
      >
        <Flex vertical gap="middle">
          <Failure error={mutation.error} />
          <Typography.Text>{request.relative_path || request.document_id}</Typography.Text>
          <Typography.Paragraph>
            使用此处理请求保存的原文快照和当前配置，创建新的后台请求；完成前保留当前可用版本。
          </Typography.Paragraph>
          <Checkbox
            checked={intent?.replace || false}
            disabled={mutation.isPending}
            onChange={(event: { target: { checked: boolean } }) =>
              setIntent({ key: crypto.randomUUID(), replace: event.target.checked })
            }
          >
            允许替换人工修改
          </Checkbox>
          {intent?.replace && (
            <Alert type="warning" showIcon message="成功后将替换当前文档的人工编辑及分块移除结果，历史版本仍保留。" />
          )}
        </Flex>
      </Modal>
    </>
  );
}
