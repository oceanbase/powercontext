#!/usr/bin/env python3
"""Run the production Server stack with a deterministic evaluation pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from powercontext.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.server.runtime import create_server_app
from powercontext.server.settings import HttpSettings, ServerSettings, SQLiteStorageSettings
from powercontext.sources import ContentSource


class ContentCandidatePipeline:
    """Turn captured Codex prompts into traceable evaluation Memory."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="task-outcome",
                text=source.content,
                sources=(source,),
                reason="captured from the Codex UserPromptSubmit hook",
            )
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    settings = ServerSettings(
        http=HttpSettings(host="127.0.0.1", port=arguments.port),
        storage=SQLiteStorageSettings(path=arguments.database),
    )
    uvicorn.run(
        create_server_app(settings=settings, candidate_pipeline=ContentCandidatePipeline()),
        host=settings.http.host,
        port=settings.http.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
