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
import { Alert, Button, Dropdown, Empty, Flex, Space, Spin, Typography } from "@oceanbase/design";
import { BookOutlined, ReloadOutlined, SwapOutlined } from "@oceanbase/icons";
import { PageContainer } from "@oceanbase/ui";
import { useDefault, useLibraries, useRefresh, useSetDefault, useSpaces } from "./hooks";
import { Failure } from "./components/Feedback";
import { Create } from "./actions";
import { Documents } from "./pages/Documents";
import { Reader } from "./pages/Reader";
import { Retrieval } from "./pages/Retrieval";
import { Settings } from "./pages/Settings";
import type { Location } from "./types";
function Workspace({ space }: { space: string }) {
  const libraries = useLibraries(space);
  const [selected, setSelected] = useState("");
  const [tab, setTab] = useState("documents");
  const [location, setLocation] = useState<Location>();
  const items = libraries.data?.items || [];
  const kb = items.find((item) => item.scope_id === selected);
  return (
    <div className="workspace">
      <aside className="library-nav" aria-label="知识库列表">
        <Flex vertical gap="middle">
          <Flex justify="space-between" align="center">
            <Typography.Text strong>知识库</Typography.Text>
            <Create kind="kb" space={space} />
          </Flex>
          <Failure error={libraries.error} />
          {libraries.isPending && <Spin />}
          {items.map((item) => (
            <Button
              block
              key={item.scope_id}
              className="library-link"
              type={item.scope_id === selected ? "link" : "text"}
              icon={<BookOutlined aria-hidden />}
              aria-pressed={item.scope_id === selected}
              aria-label={item.value.title}
              title={item.value.title}
              onClick={() => {
                setSelected(item.scope_id);
                setLocation(undefined);
              }}
            >
              <span className="library-name">{item.value.title}</span>
            </Button>
          ))}
          {!libraries.isPending && !items.length && (
            <Typography.Text type="secondary">在此空间新建知识库，然后导入文档。</Typography.Text>
          )}
        </Flex>
      </aside>
      <main className="workspace-main">
        {location && (
          <Reader key={`${location.scope}:${location.id}`} location={location} onBack={() => setLocation(undefined)} />
        )}
        <div hidden={!!location}>
          {kb ? (
            <PageContainer
              className="kb-page"
              title={kb.value.title}
              tabActiveKey={tab}
              onTabChange={setTab}
              tabList={[
                { key: "documents", tab: "文档" },
                { key: "retrieval", tab: "检索测试" },
                { key: "settings", tab: "设置" },
              ]}
            >
              {tab === "documents" && <Documents key={kb.scope_id} kb={kb} onOpen={setLocation} />}
              {tab === "retrieval" && (
                <Retrieval
                  key={kb.scope_id}
                  space={space}
                  libraries={items}
                  initialScope={kb.scope_id}
                  onOpen={setLocation}
                />
              )}
              {tab === "settings" && <Settings key={kb.scope_id} kb={kb} />}
            </PageContainer>
          ) : (
            <div className="workspace-empty">
              <Empty description="选择左侧知识库，浏览文档或测试检索" />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
export function App() {
  const spaces = useSpaces();
  const defaults = useDefault();
  const changeDefault = useSetDefault();
  const refresh = useRefresh();
  const [selected, setSelected] = useState<string>();
  const preferred = selected ?? (defaults.data?.available ? defaults.data.space_scope_id : "");
  const space = spaces.data?.items.find((item) => item.space_scope_id === preferred);
  return (
    <Flex vertical className="kb-app">
      <header className="space-bar">
        <Flex justify="space-between" align="center" wrap gap="middle">
          <Space>
            <Typography.Text type="secondary">空间</Typography.Text>
            <Dropdown
              trigger={["click"]}
              menu={{
                selectedKeys: space ? [space.space_scope_id] : [],
                items: (spaces.data?.items || []).map((item) => ({ key: item.space_scope_id, label: item.title })),
                onClick: ({ key }) => {
                  setSelected(key);
                  changeDefault.reset();
                },
              }}
            >
              <Button type="text" aria-label="切换空间" icon={<SwapOutlined aria-hidden />}>
                {space?.title || "请选择空间"}
              </Button>
            </Dropdown>
            {space?.space_scope_id === defaults.data?.space_scope_id && (
              <Typography.Text type="secondary">默认空间</Typography.Text>
            )}
          </Space>
          <Space>
            <Button
              size="small"
              disabled={!space || !defaults.data || space.space_scope_id === defaults.data.space_scope_id}
              loading={changeDefault.isPending}
              onClick={() => {
                if (space && defaults.data)
                  changeDefault.mutate({ space: space.space_scope_id, version: defaults.data.version });
              }}
            >
              设为默认空间
            </Button>
            <Create kind="space" />
            <Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新知识库" onClick={() => refresh()} />
          </Space>
        </Flex>
      </header>
      <Failure error={spaces.error || defaults.error || changeDefault.error} />
      {spaces.isPending || defaults.isPending ? (
        <div className="workspace-empty">
          <Spin />
        </div>
      ) : space ? (
        <Workspace key={space.space_scope_id} space={space.space_scope_id} />
      ) : (
        <div className="workspace-empty">
          <Alert type="info" showIcon message="默认空间不可访问，请选择其他空间并设置默认空间。" />
        </div>
      )}
    </Flex>
  );
}
