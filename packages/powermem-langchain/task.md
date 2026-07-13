背景
在面向 agent 的应用中，模型本身通常只看到当前请求和有限上下文。Memory 的作用是把用户偏好、历史事实、长期任务状态等信息保存在模型调用之外，并在后续交互中按需取回。一个可用的 memory 集成通常需要完成两件事：在生成前检索与当前输入相关的记忆，在生成后把新的交互或可沉淀的信息写回记忆系统。
这类能力可以让 agent 在多轮或跨会话场景中表现得更连续：它不需要每次都重新询问已经明确的偏好，也可以围绕用户长期目标、项目背景或领域上下文生成更稳定的回答。与此同时，memory 也需要被谨慎地接入模型上下文，避免无关历史污染当前任务，或把框架内部状态和业务身份耦合在一起。
PowerMem 已经提供了面向长期记忆的 SDK 和服务能力，但早期 LangChain 集成更接近手写封装或链式调用示例。LangChain v1 提供了 middleware 机制，可以在 agent 生命周期中加载上下文、调整模型请求，并在运行结束后处理结果。这个任务希望你基于 middleware 重新设计 PowerMem 与 LangChain agent 的集成方式。
这个方向的意义不只在于完成一个 create_agent 示例。LangChain middleware 是控制 agent 执行流程的扩展点，Deep Agents 等相关项目也通过 middleware 组合规划、上下文和记忆等能力。因此，一个清晰的 PowerMem middleware 设计更容易复用到更复杂的 agent 形态中，而不是停留在单个示例脚本里。
本包提供的是暑期学校任务骨架，不是完整实现。你会看到：
● 一个独立的 Python package：packages/powermem-langchain
● 一个可导入、可实例化的 no-op middleware 骨架
● 一组基础行为测试
● 一个使用 OpenAI 模型的端到端示例
你可以根据自己的实现需要调整骨架，但记忆检索、上下文注入和交互写回都应通过 LangChain middleware hooks 完成。请避免把任务实现成只满足测试的特例代码。
目标
实现 PowerMemMiddleware，让 LangChain v1 agent 可以使用 PowerMem 作为长期记忆层。
最小使用形态如下：
from langchain.agents import create_agent
from powermem import create_memory
from powermem_langchain import PowerMemMiddleware

memory = create_memory()

agent = create_agent(
    model="openai:gpt-4o-mini",
    tools=[],
    middleware=[
        PowerMemMiddleware(
            memory=memory,
            user_id="user123",
            search_limit=5,
            save_interactions=True,
        )
    ],
)
实现要求
你的实现至少需要覆盖以下行为：
● 在模型调用前，根据最新用户消息调用 PowerMem 检索相关记忆。
● 将检索到的记忆注入模型可见上下文。
● 在 agent 运行结束后，将用户消息和助手回复写回 PowerMem。
● 当 save_interactions=False 时，不写回交互。
● 使用构造参数中的显式 user_id 作为用户身份。
● PowerMem 检索失败时，默认保持 agent 可继续运行。
● 同步和异步 agent 调用路径都应能工作。
已提供的测试
从仓库根目录运行：
uv run --no-project \
  --python 3.11 \
  --with-editable "." \
  --with-editable "packages/powermem-langchain[test]" \
  pytest packages/powermem-langchain/tests -q
测试使用本地 SQLite PowerMem、noop LLM provider 和 mock embedder，不需要 OpenAI API key，也不需要 OceanBase。
这些测试是基础验收，不是完整设计规格。它们主要验证：
● PowerMemMiddleware 可以从公开入口导入。
● 检索出的 PowerMem 记忆会出现在模型输入中。
● agent 运行后会按需写回 PowerMem。
● 禁用写回时不会产生新记忆。
● 检索失败时 agent 仍能运行。
● 异步 agent 调用也能读取记忆。
OpenAI 示例
示例文件位于：
packages/powermem-langchain/examples/openai_agent.py
它用于在真实模型调用下检查完整流程：
1. 创建 PowerMem 实例。
2. 为演示用户写入一条初始记忆。
3. 创建带有 PowerMemMiddleware 的 LangChain agent。
4. 通过 langchain-openai 调用 OpenAI chat model。
5. 打印 agent 调用前后的 PowerMem 检索结果。
最小环境变量示例：
export OPENAI_API_KEY="..."
export LLM_PROVIDER=openai
export LLM_API_KEY="$OPENAI_API_KEY"
export LLM_MODEL=gpt-4o-mini
export DATABASE_PROVIDER=sqlite
export SQLITE_PATH="./data/powermem_langchain_demo.db"
运行示例：
uv run --no-project \
  --python 3.11 \
  --with-editable "." \
  --with-editable "packages/powermem-langchain[example]" \
  python packages/powermem-langchain/examples/openai_agent.py \
    --user-id summer-school-demo
当前 no-op middleware 骨架可以被实例化，但不会产生期望的记忆注入和写回行为。完成实现后，示例输出应能体现：
● agent 调用前可以检索到种子记忆。
● 模型回答会受到相关记忆影响。
● agent 调用后可以检索到新的交互记忆。
建议提交内容
请提交 Pull Request，并将目标分支设置为 vldb_2026。建议保持修改集中在这个 package 内：
packages/powermem-langchain/
  README.md
  examples/openai_agent.py
  pyproject.toml
  src/powermem_langchain/
    __init__.py
    middleware.py
  tests/test_middleware.py
提交前建议确认：
● 测试通过。
● 示例可以在配置 OpenAI 和 PowerMem 后运行。
● 实现没有依赖全局隐式配置来获取业务用户身份。
● 使用的 hook 和状态字段有清晰用途。
● 新增测试覆盖了你引入的行为，而不是重复测试已有路径。
提交时请注意：
● 不需要修改主仓库已有的旧版 LangChain 示例或集成文档。
● 不需要提交真实 API key、.env 文件、SQLite 数据库文件或测试缓存。
● 如果调整了公开构造参数或行为契约，请同步更新测试和示例。
● 提交信息建议使用语义化提交，例如 feat: implement powermem langchain middleware。


fork相关仓库，切换到对应分支(vldb_2026)，提交代码给官方仓库，通过github action跑分。
项目	代码仓库	分支
powermem	https://github.com/oceanbase/powermem	vldb_2026
seekdb	https://github.com/oceanbase/seekdb	vldb_2026
注意fork指定分支！
PR 创建好后，可以在统计文档中进行更新，我会用脚本统计大家的成绩
https://oceanbase.yuque.com/g/org-wiki-obtech-vh7w9r/hc3pio/snoeve4gf18ksg85/collaborator/join?token=5S8Mmaa7BtSwfRBA&source=doc_collaborator# 邀请你共同编辑表格《队伍与PR统计》
算分原则
总分100分，每个题目25分。
每个小题的满分也按照100分算，会按照0.25系数折算到总分中。
题目	规则
PowerMem 的 LangChain 中间件集成	Public import contract: PASS (gate)
Retrieve memory before model call: PASS (35/35)
Persist interaction after agent run: PASS (25/25)
Disable interaction persistence: PASS (10/10)
Fail-open on search error: PASS (15/15)
Async memory injection: PASS (15/15)
