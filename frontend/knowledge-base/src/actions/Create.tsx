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
import { Button, Form, Input, Modal } from "@oceanbase/design";
import { PlusOutlined } from "@oceanbase/icons";
import { useCreate } from "../hooks";
import { Failure } from "../components/Feedback";
export function Create({ kind, space }: { kind: "space" | "kb"; space?: string }) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const intent = useRef<Record<string, unknown>>();
  const mutation = useCreate(kind);
  const label = kind === "space" ? "新建空间" : "新建知识库";
  const close = () => {
    if (!mutation.isPending) {
      setOpen(false);
      form.resetFields();
      intent.current = undefined;
      mutation.reset();
    }
  };
  return (
    <>
      <Button
        icon={<PlusOutlined aria-hidden />}
        size="small"
        disabled={kind === "kb" && !space}
        onClick={() => setOpen(true)}
      >
        {label}
      </Button>
      <Modal
        title={label}
        open={open}
        onCancel={close}
        onOk={() => form.submit()}
        okText={intent.current ? "重试创建" : "创建"}
        cancelText="取消"
        confirmLoading={mutation.isPending}
        maskClosable={false}
      >
        <Failure error={mutation.error} />
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={async (values) => {
            intent.current ??= {
              title: values.title.trim(),
              idempotency_key: crypto.randomUUID(),
              ...(kind === "kb" ? { space_scope_id: space } : {}),
            };
            try {
              await mutation.mutateAsync(intent.current);
              close();
            } catch {
              /* Keep the accepted parameters for retry. */
            }
          }}
        >
          <Form.Item
            name="title"
            label={kind === "space" ? "空间名称" : "知识库名称"}
            rules={[{ required: true, whitespace: true, message: "请输入名称" }, { max: 200 }]}
          >
            <Input
              autoFocus
              disabled={!!intent.current}
              placeholder={kind === "space" ? "例如：研发团队" : "例如：运维手册"}
              maxLength={200}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
