"""Run a PowerMem-backed LangChain agent with an OpenAI chat model.

The synchronous example uses an explicit application user ID, seeds PowerMem,
invokes a LangChain agent, and displays search results before and after the
middleware writes back the interaction. The middleware also supports async
agents. PowerMem search and write-back are best-effort: failures are logged and
do not replace the model response. Retrieved memories are untrusted reference
context and must not be treated as system instructions.
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
DEFAULT_SEED_MEMORY = "The user prefers concise answers with database-focused examples."


class DemoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="POWERMEM_LANGCHAIN_",
        extra="ignore",
    )

    openai_api_key: str | None = Field(
        default=None,
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
    memories = result.get("results", [])
    print(title)
    if not memories:
        print("  - No memories found.")
        return

    for item in memories[:5]:
        memory = item.get("memory") or item.get("content") or str(item)
        print(f"  - {memory}")


def main() -> None:
    settings = DemoSettings()
    args = parse_args(settings)

    print("PowerMem LangChain OpenAI demo")
    print(f"user_id: {args.user_id}")
    print(f"model: {args.model}")

    try:
        memory = create_memory()
    except Exception as exc:
        raise SystemExit(
            "Failed to create PowerMem memory. Configure PowerMem first, for "
            "example by setting LLM_PROVIDER, LLM_API_KEY, DATABASE_PROVIDER, "
            "EMBEDDING_PROVIDER, EMBEDDING_API_KEY, EMBEDDING_MODEL, and "
            "EMBEDDING_DIMS, or by using a valid .env file."
        ) from exc

    if not args.skip_seed:
        memory.add(args.seed_memory, user_id=args.user_id, infer=False)
        print(f"seed_memory: {args.seed_memory}")

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

    after = memory.search(
        args.prompt,
        user_id=args.user_id,
        limit=args.search_limit,
    )
    print_search_results("memories_after_agent:", after)


if __name__ == "__main__":
    main()
