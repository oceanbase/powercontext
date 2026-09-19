/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

export const messages = {
  zh: {
    home: "首页",
    connections: "连接",
    memories: "我的记忆",
    settings: "设置",
    menu: "导航菜单",
    tagline: "记录重要的事，让每次查找更轻松。",
    quick: "快速记录",
    noteHint: "记下你的偏好、约定，或以后会用到的信息。",
    note: "记忆内容",
    save: "保存记忆",
    scope: "保存范围：尚未选择",
    current: "当前连接",
    disconnected: "尚未连接",
    connect: "连接已有服务",
    connectHint: "连接已有的 PowerContext Server 后，选择范围即可保存和查找。",
    foundation: "工程预览",
    foundationHint: "连接服务并选择范围后，即可保存、全文搜索和阅读记忆。",
    find: "查找记忆",
    query: "搜索关键词",
    search: "查找",
    findHint: "输入关键词，找回明确保存过的内容。",
    empty: "还没有可查看的记忆",
    emptyHint:
      "连接服务并选择范围后，使用全文搜索查找内容。这里不会自动加载全部记忆。",
    noConnections: "尚未添加连接",
    connectionIntro: "管理你已有的服务，确认信息保存的位置。",
    boundary:
      "桌面不会安装或启动 Server。关闭窗口将退出桌面，不会停止独立运行的服务。",
    details: "连接状态详情",
    unverified: "未验证",
    identity: "身份与访问",
    readiness: "就绪状态",
    compatibility: "兼容性",
    tls: "TLS 信任",
    language: "语言",
    theme: "主题",
    light: "浅色",
    dark: "深色",
    system: "跟随系统",
    diagnostics: "本地诊断",
    diagnosticsHint: "尚未执行本地 CLI 检查。这不代表远程服务不可用。",
    version: "桌面版本",
    native: "原生宿主",
    unavailable: "未连接原生宿主",
    nativeReady: "已就绪",
    privacy: "正文和搜索结果只在内存中使用；凭据由系统凭据库或本次会话管理。",
    tip: "使用提示",
    tipBody: "一次记录一件具体的事，之后更容易找到。",
    searchLimit: "全文搜索，最多返回 10 条。",
    skip: "跳到主要内容",
  },
  en: {
    home: "Home",
    connections: "Connections",
    memories: "My memories",
    settings: "Settings",
    menu: "Navigation",
    tagline: "Keep what matters. Find it when you need it.",
    quick: "Quick note",
    noteHint: "Record a preference, an agreement, or something to remember.",
    note: "Memory text",
    save: "Save memory",
    scope: "Scope: none selected",
    current: "Current connection",
    disconnected: "Not connected",
    connect: "Connect an existing server",
    connectHint:
      "Connect to an existing PowerContext Server, then select a scope to save and search.",
    foundation: "Foundation preview",
    foundationHint:
      "Connect a Server and select a Scope to save, search and read memories.",
    find: "Find memories",
    query: "Search keywords",
    search: "Search",
    findHint: "Use keywords to find content you explicitly saved.",
    empty: "No memories to display",
    emptyHint:
      "Connect a server and select a scope to search. This page does not automatically load all memories.",
    noConnections: "No connections added",
    connectionIntro:
      "Manage existing servers and know where your information is saved.",
    boundary:
      "Desktop does not install or start a Server. Closing the window exits Desktop and leaves independent services running.",
    details: "Connection status details",
    unverified: "Not verified",
    identity: "Identity and access",
    readiness: "Readiness",
    compatibility: "Compatibility",
    tls: "TLS trust",
    language: "Language",
    theme: "Theme",
    light: "Light",
    dark: "Dark",
    system: "System",
    diagnostics: "Local diagnostics",
    diagnosticsHint:
      "Local CLI checks have not run. This does not indicate a remote service failure.",
    version: "Desktop version",
    native: "Native host",
    unavailable: "Native host unavailable",
    nativeReady: "Ready",
    privacy:
      "Memory text and search results stay in memory. Credentials use the system vault or this session.",
    tip: "A useful habit",
    tipBody: "Keep each note focused on one thing so it is easier to find.",
    searchLimit: "Full-text search, up to 10 results.",
    skip: "Skip to main content",
  },
};
export type Language = keyof typeof messages;
