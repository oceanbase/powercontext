# Week 1 完成 - 测试指南

## ✅ 第一周交付物检查清单

- ✅ 完整的项目目录结构
- ✅ 所有配置文件 (package.json, tsconfig.json, vite.config.ts)
- ✅ 类型定义文件 (Memory, Settings, Message)
- ✅ Storage Service 骨架
- ✅ Background Service Worker 骨架
- ✅ Content Script 骨架
- ✅ Popup UI 骨架 (React)
- ✅ Sidebar 和 Options 页面骨架
- ✅ 占位图标文件 (16x16, 48x48, 128x128)
- ✅ 成功构建到 dist 目录

## 在 Chrome 中测试扩展

### 步骤 1: 加载扩展

1. 打开 Chrome 浏览器
2. 访问 `chrome://extensions/`
3. 在右上角启用 **"开发者模式"**
4. 点击 **"加载已解压的扩展程序"**
5. 选择项目的 `dist` 目录 (d:\powermem\browser-plugin\dist)
6. 扩展应该会出现在列表中

### 步骤 2: 验证基本功能

#### 2.1 检查扩展图标
- 扩展图标应该出现在 Chrome 工具栏中
- 图标应该是紫色背景，带有字母 "P"

#### 2.2 测试 Popup
- 点击扩展图标
- 应该会弹出一个小窗口
- 显示 "PowerMem" 标题和 "Extension loaded successfully!" 消息
- 显示 "UI implementation in Week 3"

#### 2.3 测试 Options 页面
- 在扩展列表中，点击扩展的 "详细信息"
- 点击 "扩展选项"
- 应该打开一个新标签页
- 显示 "PowerMem Settings" 标题
- 显示 "Settings page implementation in Week 4"

#### 2.4 测试 Sidebar (可选,Chrome 114+)
- 右键点击扩展图标
- 如果浏览器支持,应该有 "打开侧边栏" 选项
- 显示 "PowerMem Sidebar" 标题
- 显示 "Sidebar implementation in Week 5"

#### 2.5 检查 Background Service Worker
- 在扩展页面 (`chrome://extensions/`)
- 点击扩展的 "Service Worker" 链接
- 应该打开开发者工具
- Console 中应该显示: `[PowerMem] Background service worker loaded`

#### 2.6 测试右键菜单
- 在任意网页上选中一段文本
- 右键点击选中的文本
- 应该看到 "Save to PowerMem" 菜单项
- 点击菜单项,在 Service Worker 的 Console 中应该显示日志

#### 2.7 测试 Content Script
- 在任意网页上打开开发者工具 (F12)
- 刷新页面
- Console 中应该显示: `[PowerMem] Content script loaded on: [URL]`
- 选中网页上的文本 (超过10个字符)
- Console 中应该显示: `[PowerMem] Text selected: ...`

## 预期结果

✅ 所有以上测试应该能够正常工作
✅ Console 中没有报错
✅ 扩展能够正常加载和运行

## 已知限制

- 图标是简单的占位符,需要专业设计
- 所有 UI 页面只显示占位文本
- Storage Service 方法尚未实现 (抛出 "Not implemented" 错误)
- 没有实际的记忆保存和搜索功能

这些功能将在后续周次实现:
- Week 2: 记忆捕获和 API 通信
- Week 3: Popup UI 实现
- Week 4: Options 页面实现
- Week 5: Sidebar 和高级功能
- Week 6: 性能优化和测试

## 下一步

第一周基础架构已完成!可以继续进行 Week 2 的开发:
- 实现 Storage Service
- 实现 API Client
- 实现记忆捕获逻辑
- 实现消息处理机制
