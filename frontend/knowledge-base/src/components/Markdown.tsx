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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Flex, Segmented, Typography } from "@oceanbase/design";
// A template keeps raw markup inert. Only text nodes enter the React tree.
function htmlText(html: string) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const fragment = template.content;
  fragment.querySelectorAll("script, style, template, noscript, head, iframe, [hidden]").forEach((node) => node.remove());
  fragment.querySelectorAll("br, p, div, li, th, td, tr").forEach((node) => node.after(" "));
  return fragment.textContent || "";
}
export function Markdown({ text, controls = true }: { text: string; controls?: boolean }) {
  const [mode, setMode] = useState("preview");
  return (
    <Flex vertical gap="middle">
      {controls && (
        <Flex justify="end">
          <Segmented
            aria-label="正文展示方式"
            value={mode}
            onChange={(value) => setMode(String(value))}
            options={[
              { label: "阅读", value: "preview" },
              { label: "Markdown 源码", value: "source" },
            ]}
          />
        </Flex>
      )}
      {mode === "source" ? (
        <pre className="markdown-source">{text}</pre>
      ) : (
        <article className="markdown-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            remarkRehypeOptions={{ handlers: { html: (_, node) => ({ type: "text", value: htmlText(node.value) }) } }}
            skipHtml
            components={{
              a: ({ href, children }) =>
                href && /^https?:\/\//i.test(href) ? (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                ) : (
                  <span title={href}>{children}</span>
                ),
              img: ({ alt }) => <Typography.Text type="secondary">[图片引用：{alt || "图片"}]</Typography.Text>,
            }}
          >
            {text}
          </ReactMarkdown>
        </article>
      )}
    </Flex>
  );
}
