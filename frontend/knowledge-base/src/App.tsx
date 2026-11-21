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
import { useState, type ReactNode } from "react";
import { Alert, Button, Dropdown, Empty, Flex, Space, Spin, Typography } from "@oceanbase/design";
import { ReloadOutlined, SwapOutlined } from "@oceanbase/icons";
import { PageContainer } from "@oceanbase/ui";
import { useDefault, useHostContext, useLibrary, useLibraries, useRefresh, useSetDefault, useSpaces } from "./hooks";
import { ProcessingWatcher } from "./components/ProcessingWatcher";
import { Failure } from "./components/Feedback";
import * as Actions from "./actions";
import { Documents } from "./pages/Documents";
import { Reader } from "./pages/Reader";
import { Retrieval } from "./pages/Retrieval";
import { Settings } from "./pages/Settings";
import type { Location } from "./types";
function Workspace({
  space,
  initialScope = "",
  navigation,
  actions,
}: {
  space: string;
  initialScope?: string;
  navigation: ReactNode;
  actions: ReactNode;
}) {
  const libraries = useLibraries(space);
  const [selected, setSelected] = useState(initialScope);
  const [tab, setTab] = useState("documents");
  const [location, setLocation] = useState<Location>();
  const items = libraries.data?.items || [];
  const kb = items.find((item) => item.scope_id === selected);
  const displayedLibrary = items.find((item) => item.scope_id === (location?.scope ?? selected));
  return (
    <Flex vertical className="workspace">
      <header className="space-bar">
        <Flex justify="space-between" align="center" wrap gap="middle">
          <Space wrap>
            {navigation}
            <Typography.Text type="secondary" aria-hidden>
              /
            </Typography.Text>
            <Dropdown
              trigger={["click"]}
              menu={{
                selectedKeys: [location?.scope ?? selected],
                items: items.map((item) => ({ key: item.scope_id, label: item.value.title })),
                onClick: ({ key }) => {
                  setSelected(key);
                  setLocation(undefined);
                },
              }}
              disabled={!items.length}
            >
              <Button
                type="text"
                aria-label="切换知识库"
                icon={<SwapOutlined aria-hidden />}
                loading={libraries.isPending}
              >
                <span className="context-label" title={displayedLibrary?.value.title}>
                  {displayedLibrary?.value.title || "选择知识库"}
                </span>
              </Button>
            </Dropdown>
            <Actions.Create kind="kb" space={space} />
          </Space>
          {actions}
        </Flex>
      </header>
      <Failure error={libraries.error} />
      <main className="workspace-main">
        {location && (
          <Reader
            key={`${location.scope}:${location.id}`}
            location={location}
            libraryTitle={displayedLibrary?.value.title}
            onBack={() => setLocation(undefined)}
          />
        )}
        <div hidden={!!location}>
          {kb ? (
            <PageContainer
              className="kb-page"
              title={false}
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
              {libraries.isPending ? (
                <Spin />
              ) : (
                <Empty
                  description={items.length ? "在顶部选择知识库，浏览文档或测试检索" : "此空间还没有知识库，请在顶部新建知识库"}
                />
              )}
            </div>
          )}
        </div>
      </main>
    </Flex>
  );
}
export function App() {
  const spaces = useSpaces();
  const defaults = useDefault();
  const changeDefault = useSetDefault();
  const refresh = useRefresh();
  const host = useHostContext();
  const hostLibrary = useLibrary(host.data?.scopeId);
  const [selected, setSelected] = useState<string>();
  const preferred =
    selected ?? hostLibrary.data?.space_scope_id ?? (defaults.data?.available ? defaults.data.space_scope_id : "");
  const space = spaces.data?.items.find((item) => item.space_scope_id === preferred);
  const initialScope = selected === undefined ? hostLibrary.data?.scope_id : undefined;
  const resolvingHost = selected === undefined && (host.isPending || (!!host.data?.scopeId && hostLibrary.isPending));
  const hostError = selected === undefined ? host.error || hostLibrary.error : null;
  const navigation = (
    <Space>
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
          <span className="context-label" title={space?.title}>
            {space?.title || "选择知识空间"}
          </span>
        </Button>
      </Dropdown>
      {space?.space_scope_id === defaults.data?.space_scope_id && (
        <Typography.Text type="secondary">默认空间</Typography.Text>
      )}
    </Space>
  );
  const actions = (
    <Space>
      <Button
        size="small"
        aria-label="设为默认空间"
        disabled={!space || !defaults.data || space.space_scope_id === defaults.data.space_scope_id}
        loading={changeDefault.isPending}
        onClick={() => {
          if (space && defaults.data)
            changeDefault.mutate({ space: space.space_scope_id, version: defaults.data.version });
        }}
      >
        设为默认空间
      </Button>
      <Actions.Create kind="space" />
      <Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新知识库" onClick={() => refresh()} />
    </Space>
  );
  return (
    <Flex vertical className="kb-app">
      <ProcessingWatcher />
      {(spaces.isPending || defaults.isPending || resolvingHost || hostError || !space) && (
        <header className="space-bar">
          <Flex justify="space-between" align="center" wrap gap="middle">
            {navigation}
            {actions}
          </Flex>
        </header>
      )}
      <Failure error={spaces.error || defaults.error || changeDefault.error || hostError} />
      {selected === undefined && host.data?.scopeId && hostLibrary.data === null && (
        <Alert
          role="status"
          type="info"
          showIcon
          message="当前内容范围未关联可访问的知识库。下方显示默认空间，请选择知识库或切换空间。"
        />
      )}
      {spaces.isPending || defaults.isPending || resolvingHost ? (
        <div className="workspace-empty">
          <Spin />
        </div>
      ) : hostError ? null : space ? (
        <Workspace
          key={`${space.space_scope_id}:${initialScope || ""}`}
          space={space.space_scope_id}
          initialScope={initialScope}
          navigation={navigation}
          actions={actions}
        />
      ) : (
        <div className="workspace-empty">
          <Alert type="info" showIcon message="默认空间不可访问，请选择其他空间并设置默认空间。" />
        </div>
      )}
    </Flex>
  );
}
