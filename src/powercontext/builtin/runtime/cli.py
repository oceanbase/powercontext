"""CLI commands for the configured Builtin runtime instance."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from pydantic import BaseModel

from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.models import MemorySearchMode, PreparedContextSchema
from powercontext.builtin.runtime.settings import BuiltinSettings

HELP_OPTION_NAMES = ("-h", "--help")

app = typer.Typer(
    name="builtin",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Operate the configured Builtin runtime instance.",
    no_args_is_help=True,
)


class _BuiltinCapabilities(BaseModel):
    database: str
    memory_extraction: bool
    experience_generation: bool
    managed_skill_generation: bool
    external_skill_registry: bool
    handoff_generation: bool
    memory_search_modes: tuple[MemorySearchMode, ...]
    context_versions: tuple[PreparedContextSchema, ...]


@app.callback()
def main() -> None:
    """Operate the configured Builtin runtime instance."""


@app.command()
def capabilities(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Open the configured database and report available behavior."""

    asyncio.run(_show_capabilities(BuiltinSettings(), json_output=json_output))


async def _show_capabilities(settings: BuiltinSettings, *, json_output: bool) -> None:
    config = BuiltinConfig(
        runtime=settings.runtime,
        database=settings.database,
        inference=settings.inference,
        external_skills=settings.external_skills,
    )
    async with open_builtin_runtime(config) as runtime:
        runtime_capabilities = await runtime.capabilities()
    capabilities = _BuiltinCapabilities(
        database=settings.database.kind,
        memory_extraction=runtime_capabilities.memory_extraction,
        experience_generation=runtime_capabilities.experience_generation,
        managed_skill_generation=runtime_capabilities.managed_skill_generation,
        external_skill_registry=runtime_capabilities.external_skill_registry,
        handoff_generation=runtime_capabilities.handoff_generation,
        memory_search_modes=runtime_capabilities.memory_search_modes,
        context_versions=runtime_capabilities.context_versions,
    )
    if json_output:
        typer.echo(capabilities.model_dump_json())
        return
    typer.echo(f"Database: {capabilities.database}")
    typer.echo(f"Memory extraction: {'enabled' if capabilities.memory_extraction else 'disabled'}")
    typer.echo(f"Experience generation: {'enabled' if capabilities.experience_generation else 'disabled'}")
    typer.echo(f"Managed Skill generation: {'enabled' if capabilities.managed_skill_generation else 'disabled'}")
    typer.echo(f"External Skill Registry: {'enabled' if capabilities.external_skill_registry else 'disabled'}")
    typer.echo(f"Handoff generation: {'enabled' if capabilities.handoff_generation else 'disabled'}")
    modes = ", ".join(capabilities.memory_search_modes) if capabilities.memory_search_modes else "none"
    typer.echo(f"Search modes: {modes}")
    versions = ", ".join(capabilities.context_versions) if capabilities.context_versions else "none"
    typer.echo(f"Context versions: {versions}")
