# PowerMem Browser Extension

> Intelligent memory capture and retrieval for your browsing experience.

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/your-org/powermem-extension)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

## 📖 简介

PowerMem Browser Extension 是一款智能记忆捕获和检索的浏览器扩展，帮助你更好地管理和利用浏览过程中的信息。

## ✨ 核心功能

- 🚀 **快速捕获** - 一键保存选中文本
- 🔍 **语义搜索** - AI驱动的智能搜索
- 🧠 **上下文感知** - 基于当前页面的智能推荐
- 💼 **多上下文支持** - 按工作、个人、研究等分类管理记忆

## 🎯 安装

### 从 Chrome Web Store 安装（即将上线）

1. 访问 [Chrome Web Store](#)
2. 点击 "添加到 Chrome"
3. 按照安装提示完成

### 从源码安装

```bash
# 克隆仓库
git clone https://github.com/your-org/powermem-extension.git
cd powermem-extension/browser-plugin

# 安装依赖
npm install

# 构建扩展
npm run build
```

然后在 Chrome 扩展管理页面（`chrome://extensions/`）：
1. 启用 "开发者模式"
2. 点击 "加载已解压的扩展程序"
3. 选择 `dist` 目录

## 🚀 快速开始

### 1. 配置服务器

1. 点击扩展图标
2. 进入 "设置" 页面
3. 输入 PowerMem 服务器地址和 API Key
4. 点击 "测试连接" 验证配置

### 2. 保存记忆

- **方式 1**: 选中文本后点击弹出的保存按钮
- **方式 2**: 右键菜单选择 "保存到 PowerMem"
- **方式 3**: 使用快捷键 `Ctrl+Shift+S` (Windows) 或 `Cmd+Shift+S` (Mac)

### 3. 搜索记忆

1. 点击扩展图标打开弹窗
2. 在搜索框输入关键词
3. 查看搜索结果并点击查看详情

### 4. 使用侧边栏

- 点击扩展图标菜单中的 "打开侧边栏"
- 查看与当前页面相关的记忆推荐
- 快速访问最近的记忆

## 🔧 开发

### 开发环境

```bash
# 开发模式（热重载）
npm run dev

# 运行测试
npm run test

# 运行测试覆盖率
npm run test:coverage

# E2E测试
npm run test:e2e

# Lint检查
npm run lint
```

### 项目结构

```
browser-plugin/
├── src/
│   ├── background/       # 后台脚本
│   ├── content/          # 内容脚本
│   ├── popup/            # 弹窗界面
│   ├── sidebar/          # 侧边栏界面
│   ├── options/          # 设置页面
│   ├── services/         # 服务层
│   ├── store/            # 状态管理
│   ├── shared/           # 共享组件
│   └── utils/            # 工具函数
├── public/               # 静态资源
├── tests/                # 测试文件
└── dist/                 # 构建输出
```

## 📚 文档

- [用户指南](./docs/USER_GUIDE.md)
- [开发指南](./docs/DEVELOPMENT.md)
- [API 文档](./docs/API.md)
- [常见问题](./docs/FAQ.md)

## 🔑 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Shift+S` (Win) / `Cmd+Shift+S` (Mac) | 保存选中文本 |
| `Alt+P` | 打开弹窗 |
| `Alt+S` | 聚焦搜索框 |
| `Esc` | 关闭弹窗/模态框 |

## 🛠️ 技术栈

- **框架**: React 18 + TypeScript
- **构建工具**: Vite + vite-plugin-web-extension
- **状态管理**: Zustand
- **HTTP客户端**: Axios
- **测试**: Vitest + Playwright + Testing Library
- **样式**: CSS Modules

## 🤝 贡献

欢迎贡献！请查看 [CONTRIBUTING.md](./CONTRIBUTING.md) 了解详情。

## 📝 更新日志

查看 [CHANGELOG.md](./CHANGELOG.md) 了解版本更新历史。

## 📄 许可证

[MIT License](./LICENSE)

## 💬 支持

- **Email**: support@powermem.io
- **Issues**: [GitHub Issues](https://github.com/your-org/powermem-extension/issues)
- **Discord**: [加入社区](https://discord.gg/powermem)

## 🙏 致谢

感谢所有贡献者和支持者！

---

Made with ❤️ by PowerMem Team
