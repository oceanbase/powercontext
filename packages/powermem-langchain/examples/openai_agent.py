"""使用 OpenAI 聊天模型运行一个由 PowerMem 提供长期记忆的 LangChain agent。

这个示例是一个端到端的命令行检查程序。实现 PowerMemMiddleware 并配置好环境后，
它会先向 PowerMem 写入一条种子记忆，再调用由 OpenAI 驱动的 LangChain agent，
最后打印足够的信息，用来确认记忆检索、上下文注入和交互写回是否生效。
"""

from __future__ import annotations

import argparse
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from powermem import create_memory
from powermem_langchain import PowerMemMiddleware


DEFAULT_PROMPT = (
    "How should you answer my database engineering questions in future sessions?"
)
DEFAULT_SEED_MEMORY = (
    "The user prefers concise answers with database-focused examples."
)


class DemoSettings(BaseSettings):
    """从环境变量和可选的 .env 文件读取示例配置。"""

    # 专用于本示例的变量可以使用 POWERMEM_LANGCHAIN_ 前缀；extra="ignore"
    # 让 PowerMem 自己使用的其他环境变量不会触发 Pydantic 校验错误。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="POWERMEM_LANGCHAIN_",
        extra="ignore",
    )

    openai_api_key: str | None = Field(
        default=None,
        # 同时兼容 OpenAI 的标准变量和 PowerMem 示例中使用的 LLM_API_KEY。
        validation_alias=AliasChoices("OPENAI_API_KEY", "LLM_API_KEY"),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "OPENAI_LLM_BASE_URL"),
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
    )
    temperature: float = 0.0
    user_id: str = "powermem-langchain-demo"
    search_limit: int = 5
    prompt: str = DEFAULT_PROMPT
    seed_memory: str = DEFAULT_SEED_MEMORY


def parse_args(settings: DemoSettings) -> argparse.Namespace:
    """解析命令行参数，并以环境配置作为默认值。"""

    parser = argparse.ArgumentParser(
        description="Run the PowerMem LangChain middleware OpenAI demo."
    )
    parser.add_argument("--user-id", default=settings.user_id)
    parser.add_argument("--model", default=settings.openai_model)
    parser.add_argument("--temperature", type=float, default=settings.temperature)
    parser.add_argument("--search-limit", type=int, default=settings.search_limit)
    parser.add_argument("--prompt", default=settings.prompt)
    parser.add_argument("--seed-memory", default=settings.seed_memory)
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Do not add the seed memory before invoking the agent.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Disable middleware write-back for the agent interaction.",
    )
    return parser.parse_args()


def create_openai_model(settings: DemoSettings, model: str, temperature: float):
    """按需导入并创建 ChatOpenAI，缺少依赖或密钥时给出明确提示。"""

    # 延迟导入使包的基础功能和测试不必安装示例专用的 langchain-openai。
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit(
            "Install the example dependencies with "
            "`uv run --with-editable packages/powermem-langchain[example] ...`."
        ) from exc

    api_key = settings.openai_api_key
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY or LLM_API_KEY before running this demo.")

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "api_key": api_key,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url

    return ChatOpenAI(**kwargs)


def print_search_results(title: str, result: dict[str, Any]) -> None:
    """以便于人工核对的格式打印 PowerMem 检索结果。"""

    memories = result.get("results", [])
    print(title)
    if not memories:
        print("  - No memories found.")
        return

    for item in memories[:5]:
        memory = item.get("memory") or item.get("content") or str(item)
        print(f"  - {memory}")


def main() -> None:
    # 配置优先来自命令行；未传入的参数使用环境变量或类中定义的默认值。
    settings = DemoSettings()
    args = parse_args(settings)

    print("PowerMem LangChain OpenAI demo")
    print(f"user_id: {args.user_id}")
    print(f"model: {args.model}")

    try:
        # create_memory() 会读取 PowerMem 自己的数据库、LLM 和嵌入模型配置。
        memory = create_memory()
    except Exception as exc:
        raise SystemExit(
            "Failed to create PowerMem memory. Configure PowerMem first, for "
            "example by setting LLM_PROVIDER, LLM_API_KEY, DATABASE_PROVIDER, "
            "and embedding settings, or by using a valid .env file."
        ) from exc

    if not args.skip_seed:
        # infer=False 表示直接保存演示文本，不让 PowerMem 的 LLM 再做事实提取。
        memory.add(args.seed_memory, user_id=args.user_id, infer=False)
        print(f"seed_memory: {args.seed_memory}")

    # 调用 agent 前先检索一次，用于确认种子记忆已经存在。
    before = memory.search(
        args.prompt,
        user_id=args.user_id,
        limit=args.search_limit,
    )
    print_search_results("memories_before_agent:", before)

    middleware = PowerMemMiddleware(
        memory=memory,
        user_id=args.user_id,
        search_limit=args.search_limit,
        save_interactions=not args.no_save,
    )

    # middleware 应在模型调用前注入相关记忆，并在 agent 结束后按需写回交互。
    agent = create_agent(
        model=create_openai_model(settings, args.model, args.temperature),
        tools=[],
        middleware=[middleware],
    )

    print(f"prompt: {args.prompt}")
    result = agent.invoke({"messages": [HumanMessage(content=args.prompt)]})
    answer = result["messages"][-1].content
    print("assistant:")
    print(answer)

    # 再次检索同一主题，用于观察本轮用户消息和助手回复是否已被写回。
    after = memory.search(
        args.prompt,
        user_id=args.user_id,
        limit=args.search_limit,
    )
    print_search_results("memories_after_agent:", after)


if __name__ == "__main__":
    main()
