# 开发预览

在 `desktop` 目录运行 `pnpm desktop:preview`，默认打开模拟已连接界面。
仅查看网页时运行 `pnpm dev:preview`，访问 <http://127.0.0.1:1420/>。
开发窗口不提供底部模式切换栏。网页预览通过下列 URL 参数选择模式；修改参数并重新加载页面后，本次内存中的模拟编辑会清除。

普通 `pnpm desktop:dev` 默认使用真实连接。开发环境也可通过
`?preview=connected`、`?preview=disconnected` 或 `?preview=real` 明确选择模式。
`?preview=remote-only` 提供只有远程连接的接口状态：连接列表和检查报告仅包含远程服务，
当前选中远程服务的 OCP 产品研发空间，没有本地连接。
`?preview=local-only` 则只提供本地连接与其检查报告，没有远程连接。

网页预览示例：

- 模拟已连接：<http://127.0.0.1:1420/?preview=connected>
- 模拟未连接：<http://127.0.0.1:1420/?preview=disconnected>
- 真实数据模式：<http://127.0.0.1:1420/?preview=real>（实际桌面能力需通过 `pnpm desktop:dev` 使用）
- 仅远程连接：<http://127.0.0.1:1420/?preview=remote-only>
- 仅本地连接：<http://127.0.0.1:1420/?preview=local-only>

模拟连接无需执行真实服务安装，也不把模拟连接与空间写入真实连接配置。
预览并非整个桌面系统的隔离环境，语言、主题等应用设置仍使用现有设置机制。

模拟连接和空间数据集中在 `ui/src/app/preview-data.ts`，模式判断在
`ui/src/app/preview-mode.ts`，由 `ui/src/app/App.tsx` 使用解析结果。不要把更多模拟条件散落进页面样式或真实接口。
现有页面内部仍有原型数据与模拟分支，尚未统一为接口适配层。

正式构建使用 `pnpm build` 或 `pnpm desktop:build`。
生产环境的 `import.meta.env.DEV` 为 false：模式解析始终返回非模拟模式，URL 参数无法开启模拟连接；
模拟连接与空间列表返回空列表，构造模拟桌面状态会直接拒绝，模拟记忆初始数量为 0。
这不等同于已清除项目中所有历史原型代码或示例字符串。
