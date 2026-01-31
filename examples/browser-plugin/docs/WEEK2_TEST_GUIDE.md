# Week 2 功能测试指南

本指南将帮助你完成第二周实现的所有功能的测试。

## 前置准备

### 1. 确保 PowerMem 服务器运行

```bash
# 在 powermem 主目录下启动服务器
cd d:\powermem\powermem
python -m uvicorn src.server.main:app --reload --host 0.0.0.0 --port 8000
```

**验证服务器**:
- 访问 http://localhost:8000/docs
- 应该能看到 API 文档页面

### 2. 获取 API 密钥

如果 PowerMem 服务器需要 API 密钥，请先获取。如果是开发环境，可能不需要。

### 3. 加载扩展

```bash
1. 打开 Chrome: chrome://extensions/
2. 启用 "开发者模式" (右上角开关)
3. 点击 "加载已解压的扩展程序"
4. 选择目录: d:\powermem\browser-plugin\dist
5. 确认扩展已加载，图标显示在工具栏
```

## 测试步骤

### 测试 1: 扩展加载验证

**目标**: 验证扩展正确加载

**步骤**:
1. 在 chrome://extensions/ 页面查看扩展
2. 确认扩展名称: "PowerMem - Intelligent Memory Capture"
3. 确认版本: 0.1.0
4. 确认状态: 已启用

**预期结果**:
- ✅ 扩展图标显示在工具栏
- ✅ 无错误提示
- ✅ Service Worker 状态: 活动

**检查 Console**:
```javascript
// 右键扩展 → 检查 → Service Worker
// 应该看到:
// [PowerMem Background] Service Worker starting...
// [PowerMem Background] Service Worker ready
```

---

### 测试 2: 设置初始化

**目标**: 验证默认设置已创建

**步骤**:
1. 右键扩展图标 → "检查" → "Service Worker"
2. 在 Console 执行:
```javascript
chrome.storage.local.get('powermem_settings', (result) => {
  console.log('Settings:', result.powermem_settings);
});
```

**预期结果**:
- ✅ 显示默认设置对象
- ✅ server.url: "http://localhost:8000"
- ✅ user.userId: "user_xxxxx_xxxxx"
- ✅ capture.minSelectionLength: 10

---

### 测试 3: 配置扩展设置

**目标**: 配置服务器和 API 密钥

**步骤**:
1. 右键扩展图标 → "选项"
2. 如果选项页面未实现，使用 Console:
```javascript
chrome.storage.local.set({
  powermem_settings: {
    server: {
      url: 'http://localhost:8000',
      apiKey: 'your-api-key-here',  // 如果需要
      timeout: 30000
    },
    user: {
      userId: 'test_user',
      defaultAgentId: 'work'
    },
    capture: {
      autoSave: false,
      minSelectionLength: 10,
      includePageContext: true
    },
    ui: {
      theme: 'auto',
      language: 'zh'
    },
    advanced: {
      enableContextualSuggestions: true,
      maxRecentMemories: 50
    }
  }
}, () => console.log('Settings updated!'));
```

**预期结果**:
- ✅ 设置保存成功
- ✅ Console 输出: "Settings updated!"

---

### 测试 4: 文本选择功能

**目标**: 测试文本选择监听和保存按钮

**步骤**:
1. 访问任意网页 (例如: https://www.baidu.com)
2. 选择一段文本 (至少 10 个字符)
3. 观察选中文本旁边

**预期结果**:
- ✅ 显示紫色渐变的 "Save" 按钮
- ✅ 按钮位置在选中文本右侧
- ✅ 鼠标悬停时按钮有阴影效果

**检查 Console**:
```javascript
// F12 → Console (页面 Console，不是 Service Worker)
// 应该看到:
// [PowerMem Content] Script loaded on: https://...
// [PowerMem Content] Text selection handler initialized
```

---

### 测试 5: 保存记忆 (文本选择按钮)

**目标**: 通过选择按钮保存记忆

**步骤**:
1. 在网页上选择文本 (>10 字符)
2. 点击浮动的 "Save" 按钮
3. 等待 1-2 秒

**预期结果**:
- ✅ 按钮消失
- ✅ 显示绿色 Toast: "✓ 保存成功！"
- ✅ Toast 3 秒后自动消失
- ✅ 选中的文本被清除

**失败情况**:
- ❌ 红色 Toast: "✗ 无法连接到 PowerMem 服务器..."
  - 检查服务器是否运行
  - 检查 API 密钥是否正确

**验证数据**:
访问 PowerMem API 文档页面验证:
```bash
# 打开浏览器
http://localhost:8000/docs

# 使用 GET /api/v1/memories 查询
# 参数: user_id = test_user
# 应该能看到刚才保存的记忆
```

---

### 测试 6: 右键菜单保存

**目标**: 测试右键菜单功能

**步骤**:
1. 在任意网页选择文本
2. 右键点击选中的文本
3. 找到菜单项 "Save to PowerMem"
4. 点击该菜单项
5. 等待通知

**预期结果**:
- ✅ 右键菜单显示 "Save to PowerMem"
- ✅ 点击后显示 Chrome 通知 (右下角)
- ✅ 通知内容: "记忆保存成功！"
- ✅ 通知 5 秒后自动消失

**检查 Service Worker Console**:
```javascript
// 应该看到:
// [PowerMem Background] Message received: SAVE_MEMORY
// [NotificationService] ...
```

---

### 测试 7: 快捷键保存

**目标**: 测试快捷键 Ctrl+Shift+S

**步骤**:
1. 在任意网页选择文本
2. 按下快捷键:
   - Windows/Linux: `Ctrl + Shift + S`
   - Mac: `Cmd + Shift + S`
3. 等待通知

**预期结果**:
- ✅ 显示 Chrome 通知
- ✅ 通知内容: "记忆保存成功！"
- ✅ 记忆已保存到服务器

**如果快捷键不生效**:
```bash
1. 访问 chrome://extensions/shortcuts
2. 找到 PowerMem 扩展
3. 查看 "Save selected text to PowerMem" 快捷键
4. 如果冲突，可以自定义快捷键
```

---

### 测试 8: 最小字符限制

**目标**: 验证少于 10 字符不显示按钮

**步骤**:
1. 选择少于 10 个字符的文本 (例如: "hello")
2. 观察是否显示按钮

**预期结果**:
- ✅ 不显示保存按钮
- ✅ Console 无错误

---

### 测试 9: 特殊页面过滤

**目标**: 验证特殊协议页面不注入脚本

**步骤**:
1. 访问 chrome://extensions/
2. 打开 F12 Console
3. 查看 Console 输出

**预期结果**:
- ✅ 不显示 "[PowerMem Content] Script loaded"
- ✅ 页面上选择文本不显示保存按钮

---

### 测试 10: 页面上下文提取

**目标**: 验证保存的记忆包含页面信息

**步骤**:
1. 访问有 meta 标签的网页 (例如: https://github.com)
2. 选择并保存一段文本
3. 在 PowerMem API 文档查看保存的记忆
4. 检查 metadata 字段

**预期结果**:
```json
{
  "metadata": {
    "source": "browser_extension",
    "type": "web_selection",
    "url": "https://github.com/...",
    "title": "页面标题",
    "domain": "github.com",
    "timestamp": "2026-01-31T...",
    "description": "页面描述",
    "keywords": ["关键词1", "关键词2"]
  }
}
```

---

### 测试 11: 错误处理

**目标**: 测试各种错误情况

#### 11.1 服务器离线
**步骤**:
1. 停止 PowerMem 服务器
2. 尝试保存记忆

**预期结果**:
- ✅ 显示红色 Toast: "✗ 无法连接到 PowerMem 服务器..."

#### 11.2 API 密钥错误
**步骤**:
1. 设置错误的 API 密钥
2. 尝试保存记忆

**预期结果**:
- ✅ 显示红色 Toast: "✗ API 密钥无效..."

#### 11.3 网络超时
**步骤**:
1. 设置防火墙阻止连接
2. 尝试保存记忆

**预期结果**:
- ✅ 显示红色 Toast 并包含超时错误信息
- ✅ 不会无限等待

---

### 测试 12: 中文和特殊字符

**目标**: 测试各种字符类型

**步骤**:
1. 选择并保存以下文本:
   - 中文: "这是一段中文测试文本，包含标点符号。"
   - Emoji: "Hello 👋 World 🌍 Test 🚀"
   - 特殊符号: "Test <html> & \"quotes\" 'apostrophe'"
   - 混合: "测试 Test 123 😊 <tag>"

**预期结果**:
- ✅ 所有文本都能正确保存
- ✅ 特殊字符不会被转义或损坏

---

### 测试 13: 性能测试

**目标**: 验证扩展性能

#### 13.1 扩展加载时间
**步骤**:
1. 重新加载扩展
2. 观察加载时间

**预期结果**:
- ✅ 加载时间 < 1 秒
- ✅ Service Worker 立即可用

#### 13.2 文本选择响应
**步骤**:
1. 选择文本
2. 观察按钮显示延迟

**预期结果**:
- ✅ 按钮在选择后 < 100ms 显示
- ✅ 无明显卡顿

#### 13.3 大文本保存
**步骤**:
1. 选择大段文本 (1000+ 字符)
2. 保存

**预期结果**:
- ✅ 能够正常保存
- ✅ 响应时间 < 5 秒

---

## 测试清单总结

### 核心功能 ✅
- [ ] 扩展正确加载
- [ ] 设置初始化成功
- [ ] 文本选择显示按钮
- [ ] 点击按钮保存成功
- [ ] 右键菜单保存成功
- [ ] 快捷键保存成功
- [ ] 成功 Toast 显示正确

### 边界测试 ✅
- [ ] 最小字符限制 (10)
- [ ] 特殊页面过滤 (chrome://)
- [ ] 中文和特殊字符
- [ ] 大文本保存

### 错误处理 ✅
- [ ] 服务器离线提示
- [ ] API 密钥错误提示
- [ ] 网络超时处理
- [ ] 错误 Toast 显示正确

### 性能 ✅
- [ ] 扩展加载 < 1 秒
- [ ] 文本选择响应 < 100ms
- [ ] API 请求超时 30 秒

---

## 问题排查

### 问题 1: 扩展加载失败
**症状**: 加载时提示错误

**解决方案**:
1. 检查 dist 目录是否存在
2. 重新构建: `npm run build`
3. 查看具体错误信息

### 问题 2: 保存按钮不显示
**症状**: 选择文本后没有按钮

**检查**:
1. F12 Console 是否有错误
2. 是否是特殊协议页面 (chrome://)
3. 选择文本是否 >= 10 字符
4. Content Script 是否正确注入

**解决方案**:
```javascript
// 在页面 Console 执行
console.log('[Debug] Selection:', window.getSelection()?.toString());
```

### 问题 3: 保存失败
**症状**: 显示错误 Toast

**检查**:
1. PowerMem 服务器是否运行
2. 访问 http://localhost:8000/docs 是否可达
3. API 密钥是否正确
4. Service Worker Console 错误信息

**解决方案**:
```javascript
// 在 Service Worker Console 测试连接
fetch('http://localhost:8000/api/v1/health')
  .then(r => r.json())
  .then(d => console.log('Health:', d))
  .catch(e => console.error('Error:', e));
```

### 问题 4: 快捷键不工作
**症状**: 按下快捷键无反应

**检查**:
1. 访问 chrome://extensions/shortcuts
2. 查看是否有快捷键冲突
3. Service Worker Console 是否收到 command

**解决方案**:
- 自定义快捷键
- 重新加载扩展

---

## 下一步

完成所有测试后:

1. ✅ 标记所有通过的测试项
2. 📝 记录任何问题或改进建议
3. 🚀 准备开始 Week 3 开发 (Popup UI)

---

**测试完成标志**: 所有核心功能测试通过，能够稳定保存记忆到 PowerMem 服务器。
