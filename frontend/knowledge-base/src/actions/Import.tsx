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
import { Alert, Button, Flex, Form, Input, Modal, Segmented, Table, Typography, Upload } from "@oceanbase/design";
import { InboxOutlined, UploadOutlined } from "@oceanbase/icons";
import { useImport } from "../hooks";
import { Failure } from "../components/Feedback";
import type { ImportResult } from "../types";
export function Import({ scope }: { scope: string }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("upload");
  const [files, setFiles] = useState<{ file: File; key: string }[]>([]);
  const [intent, setIntent] = useState<{ root: string; paths: string[]; idempotency_key: string }>();
  const [form] = Form.useForm();
  const mutation = useImport(scope);
  const [results, setResults] = useState<ImportResult[]>();
  const uncertain = results?.some((row) => row.accepted === null);
  const close = () => {
    if (!mutation.isPending) {
      setOpen(false);
      setFiles([]);
      setResults(undefined);
      setIntent(undefined);
      form.resetFields();
      mutation.reset();
    }
  };
  const submit = async () => {
    try {
      if (mode === "upload") {
        if (files.length) setResults(await mutation.mutateAsync({ files, previous: results }));
      } else {
        const values = await form.validateFields();
        const body = intent ?? {
          root: values.root,
          paths: values.paths
            .split("\n")
            .map((p: string) => p.trim())
            .filter(Boolean),
          idempotency_key: crypto.randomUUID(),
        };
        setIntent(body);
        setResults(await mutation.mutateAsync(body));
      }
    } catch {
      /* Form or mutation renders the error in this pinned import dialog. */
    }
  };
  return (
    <>
      <Button type="primary" icon={<UploadOutlined aria-hidden />} onClick={() => setOpen(true)}>
        导入文档
      </Button>
      <Modal
        title="导入文档"
        open={open}
        onCancel={close}
        width="min(760px, 90vw)"
        footer={
          <Flex justify="end" gap="small">
            <Button onClick={close} disabled={mutation.isPending}>
              {uncertain ? "关闭" : results ? "完成" : "取消"}
            </Button>
            {(!results || uncertain) && (
              <Button
                type="primary"
                icon={mutation.isPending ? <span className="import-spinner" aria-hidden /> : undefined}
                aria-busy={mutation.isPending}
                aria-disabled={mutation.isPending || undefined}
                disabled={mode === "upload" && !files.length}
                onClick={mutation.isPending ? undefined : submit}
              >
                {uncertain ? "重试待确认文件" : "开始导入"}
              </Button>
            )}
          </Flex>
        }
        maskClosable={false}
        closable={!mutation.isPending}
      >
        <Flex vertical gap="middle">
          <Failure error={mutation.error} />
          {!results && (
            <>
              <Segmented
                value={mode}
                disabled={mutation.isPending || !!intent}
                onChange={(v) => setMode(String(v))}
                options={[
                  { label: "上传文件", value: "upload" },
                  { label: "Server 本地目录", value: "local" },
                ]}
              />
              {mode === "upload" ? (
                <Upload.Dragger
                  multiple
                  accept=".md,.markdown,.html,.htm,.png,.jpg,.jpeg,.webp"
                  disabled={mutation.isPending}
                  fileList={files.map(({ file, key }) => ({ uid: key, name: file.name, status: "done" as const }))}
                  beforeUpload={(file) => {
                    setFiles((previous) => [...previous, { file, key: crypto.randomUUID() }]);
                    return false;
                  }}
                  onRemove={(file) => {
                    setFiles((previous) => previous.filter((item) => item.key !== file.uid));
                  }}
                >
                  <p>
                    <InboxOutlined aria-hidden />
                  </p>
                  <Typography.Paragraph>拖入文件，或点击选择</Typography.Paragraph>
                  <Typography.Text type="secondary">Markdown、HTML、PNG、JPEG、WebP · 单文件不超过 16 MiB</Typography.Text>
                </Upload.Dragger>
              ) : (
                <Form form={form} layout="vertical" requiredMark={false} disabled={mutation.isPending || !!intent}>
                  <Form.Item
                    name="root"
                    label="Server 目录"
                    rules={[{ required: true, message: "请输入允许导入的 Server 目录" }]}
                    extra="需要位于管理员配置的允许导入目录内。"
                  >
                    <Input aria-label="Server 目录" placeholder="/data/documents" />
                  </Form.Item>
                  <Form.Item
                    name="paths"
                    label="相对文件路径"
                    rules={[
                      { required: true, message: "每行输入一个文件路径" },
                      {
                        validator: (_, value) =>
                          (value || "").split("\n").filter((p: string) => p.trim()).length <= 100
                            ? Promise.resolve()
                            : Promise.reject(new Error("每批最多 100 个文件")),
                      },
                    ]}
                  >
                    <Input.TextArea aria-label="相对文件路径" rows={6} placeholder={"README.md\noperations/restart.md"} />
                  </Form.Item>
                </Form>
              )}
              <Typography.Text type="secondary">
                提交后由后台解析。图片单独进行 OCR；Markdown 中的图片保留引用。
              </Typography.Text>
            </>
          )}
          {results && (
            <>
              <Alert
                type={results.every((row) => row.accepted) ? "success" : "warning"}
                showIcon
                message={`已接受 ${results.filter((row) => row.accepted).length} / ${results.length} 个文件`}
                description={uncertain
                  ? "部分文件可能已受理。在此窗口重试会沿用原文件和请求编号；关闭后可查看处理记录。"
                  : "接受导入不代表解析完成。关闭窗口后会继续跟踪处理状态；完成后更新文档，并提示重新检索。"}
              />
              <Table<ImportResult>
                size="small"
                rowKey={(_, i) => String(i)}
                dataSource={results}
                pagination={false}
                columns={[
                  { title: "文件", dataIndex: "path" },
                  { title: "结果", render: (_, row) => (row.accepted === null ? "结果待确认" : row.accepted ? "已接受" : "未接受") },
                  { title: "说明", dataIndex: "error" },
                ]}
              />
            </>
          )}
        </Flex>
      </Modal>
    </>
  );
}
