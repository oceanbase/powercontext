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
import { Alert, Button, Card, Flex, Form, InputNumber, Typography } from "@oceanbase/design";
import type { KB } from "../types";
import { useSettings } from "../hooks";
import { Failure } from "../components/Feedback";
export function Settings({ kb }: { kb: KB }) {
  const [form] = Form.useForm();
  const mutation = useSettings(kb);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    form.setFieldsValue(kb.value.chunking);
  }, [kb.version, kb.scope_id, form]);
  return (
    <Card title="切块设置">
      <Flex vertical gap="middle">
        <Failure error={mutation.error} />
        {saved && (
          <Alert
            type="success"
            showIcon
            message="配置已保存"
            description="已有文档保持原版本。需要应用新配置时，请对文档执行重新解析。"
          />
        )}
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          className="settings-form"
          initialValues={kb.value.chunking}
          onValuesChange={() => setSaved(false)}
          onFinish={async (value) => {
            try {
              await mutation.mutateAsync(value);
              setSaved(true);
            } catch {
              /* Preserve the values on conflict. */
            }
          }}
        >
          <Form.Item
            name="max_chars"
            label="最大分块字符数"
            extra="实际正文超过模型 token 限制时，解析会失败；不会静默截断。"
            rules={[{ required: true, message: "请输入字符数" }]}
          >
            <InputNumber aria-label="最大分块字符数" min={80} max={12000} precision={0} />
          </Form.Item>
          <Form.Item
            name="overlap"
            label="重叠字符数"
            dependencies={["max_chars"]}
            rules={[
              { required: true, message: "请输入重叠字符数" },
              ({ getFieldValue }) => ({
                validator: (_, value) =>
                  value < getFieldValue("max_chars")
                    ? Promise.resolve()
                    : Promise.reject(new Error("重叠字符数必须小于最大分块字符数")),
              }),
            ]}
          >
            <InputNumber aria-label="重叠字符数" min={0} max={1000} precision={0} />
          </Form.Item>
          <Button htmlType="submit" type="primary" loading={mutation.isPending}>
            保存配置
          </Button>
        </Form>
        <Typography.Text type="secondary">
          向量模型：BAAI/bge-small-zh-v1.5。本期使用插件独立配置，不支持在运行中切换模型。
        </Typography.Text>
      </Flex>
    </Card>
  );
}
