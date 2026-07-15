from __future__ import annotations

from typing import Protocol, TypeVar

from powercontext.sources.models import Source

InputT = TypeVar("InputT")
SourceT = TypeVar("SourceT", bound=Source)
ValueT_co = TypeVar("ValueT_co", covariant=True)


class SourceAdapter(Protocol[InputT, SourceT, ValueT_co]):
    """Resolve one exact input type and read one concrete Source type."""

    @property
    def input_type(self) -> type[InputT]:
        """Return the exact input type accepted by this adapter."""

        ...

    @property
    def source_type(self) -> str:
        """Return the stable plugin and routing name owned by this adapter."""

        ...

    @property
    def source_class(self) -> type[SourceT]:
        """Return the exact Source class produced and read by this adapter."""

        ...

    async def resolve(self, value: InputT, /) -> SourceT:
        """Resolve an adapter-native value without changing a catalog."""

        ...

    async def read(self, source: SourceT, /) -> ValueT_co:
        """Read the adapter-native value described by a Source."""

        ...
