"""Memory test suite."""

from powercontext.builtin.artifacts.memory import Memory, MemoryEntryVersion, MemoryService


async def current_entry(
    service: MemoryService,
    memory: Memory,
    entry_id: str | None = None,
) -> MemoryEntryVersion:
    """Select a current entry object for an object-oriented public API call."""

    entries = await service.entries(memory)
    if entry_id is None:
        return entries[0]
    return next(entry for entry in entries if entry.entry_id == entry_id)
