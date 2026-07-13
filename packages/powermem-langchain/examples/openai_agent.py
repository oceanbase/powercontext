"""
End-to-end example: LangChain agent + PowerMem long-term memory.

Demonstrates the complete flow:
  1. Create a PowerMem instance with SQLite backend
  2. Add initial "seed" memories for a demo user
  3. Wrap a LangChain agent with PowerMemMiddleware
  4. Invoke the agent — memories are retrieved and injected
  5. Show that new interactions are saved back to PowerMem

Environment variables (at minimum):
  OPENAI_API_KEY=sk-...
  LLM_PROVIDER=openai
  LLM_API_KEY=$OPENAI_API_KEY
  LLM_MODEL=gpt-4o-mini
  DATABASE_PROVIDER=sqlite
  SQLITE_PATH=./data/powermem_langchain_demo.db

Usage:
  uv run --no-project --python 3.11 \\
    --with-editable "." \\
    --with-editable "packages/powermem-langchain[example]" \\
    python packages/powermem-langchain/examples/openai_agent.py \\
    --user-id summer-school-demo
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Make sure the parent package is importable when running as a script
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from powermem import create_memory
from powermem_langchain import PowerMemMiddleware


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PowerMem + LangChain agent demo"
    )
    parser.add_argument(
        "--user-id",
        default="summer-school-demo",
        help="User identity for memory scoping",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default: temp file)",
    )
    return parser.parse_args()


def seed_memories(memory: Any, user_id: str) -> None:
    """Write a few initial facts about the user so the agent has context."""
    seeds = [
        "The user is a software engineer who mainly works with Python.",
        "The user prefers functional programming patterns.",
        "The user has been using LangChain for about six months.",
        "The user's favourite editor is Neovim with minimal plugins.",
    ]
    for text in seeds:
        memory.add(text, user_id=user_id)
    print(f"  ✓ Seeded {len(seeds)} memories for user '{user_id}'")


def show_memories(memory: Any, user_id: str, label: str) -> None:
    """Retrieve and print all stored memories for the user."""
    results = memory.search("", user_id=user_id, limit=20)
    items = results.get("results", [])
    print(f"  [{label}] {len(items)} memory(ies) for '{user_id}':")
    for i, m in enumerate(items, 1):
        content = m.get("memory", m.get("content", ""))
        score = m.get("score", "?")
        print(f"    {i:>2}. [{score:.2f}] {content[:100]}")
    print()


def build_agent(memory: Any, user_id: str) -> Any:
    """Build a LangChain agent wrapped with PowerMemMiddleware.

    This uses a minimal ReAct-style agent via langchain-openai.
    """
    from langchain.agents import create_react_agent, AgentExecutor
    from langchain_openai import ChatOpenAI
    from langchain import hub

    # -- LLM -----------------------------------------------------------
    model_name = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm = ChatOpenAI(model=model_name, temperature=0)

    # -- Tools (a trivial tool for demonstration) ---------------------
    def current_year(_: Any = None) -> str:
        return "2026"

    from langchain.tools import tool
    tools = [tool(current_year)]

    # -- Agent ---------------------------------------------------------
    prompt = hub.pull("hwchase17/react")
    agent = create_react_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    # -- Wrap with PowerMem middleware ---------------------------------
    middleware = PowerMemMiddleware(memory=memory, user_id=user_id)
    return middleware(executor)


def main() -> None:
    args = parse_args()

    # ------------------------------------------------------------------
    # 1. Create PowerMem instance (SQLite, no API key required locally)
    # ------------------------------------------------------------------
    db_path = args.db_path or os.getenv(
        "SQLITE_PATH",
        os.path.join(tempfile.gettempdir(), "powermem_langchain_demo.db"),
    )
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    print("Initialising PowerMem with SQLite backend ...")
    memory = create_memory({
        "llm": {"provider": "noop", "config": {"model": "noop"}},
        "embedder": {"provider": "none", "config": {}},
        "vector_store": {
            "provider": "sqlite",
            "config": {
                "database_path": db_path,
                "collection_name": "langchain_demo",
            },
        },
        "intelligent_memory": {"enabled": False},
    })
    print("  ✓ PowerMem ready\n")

    # ------------------------------------------------------------------
    # 2. Seed some memories
    # ------------------------------------------------------------------
    seed_memories(memory, args.user_id)
    show_memories(memory, args.user_id, "Before agent call")

    # ------------------------------------------------------------------
    # 3. Build and invoke the wrapped agent
    # ------------------------------------------------------------------
    print("Building LangChain agent with PowerMemMiddleware ...")
    wrapped = build_agent(memory, args.user_id)

    questions = [
        "What programming language do I use?",
        "What editor do I prefer?",
    ]
    for q in questions:
        print(f"\n--- User: {q} ---")
        result = wrapped.invoke({"input": q})
        print(f"Assistant: {result.get('output', result)}")

    # ------------------------------------------------------------------
    # 4. Show that new interactions were saved to PowerMem
    # ------------------------------------------------------------------
    show_memories(memory, args.user_id, "After agent call")

    print("Done.  The agent was able to use seeded memories and the")
    print("conversation turns were saved back to PowerMem automatically.")


if __name__ == "__main__":
    main()
