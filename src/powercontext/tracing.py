"""Minimal OpenTelemetry-free tracing hooks for domain code.

Domain code (the built-in Runtime and artifact services) must not depend on an OpenTelemetry
implementation. These protocols describe the small surface those services need to record bounded
spans; the ready-to-run Server adapts its ServerTracing to this protocol and injects it through
composition. When no tracer is injected, domain code creates no spans and behavior is unchanged.
"""

from __future__ import annotations

from typing import Protocol


class Span(Protocol):
    """One failure-isolated span opened by domain code."""

    def set_attributes(self, attributes: dict[str, object]) -> None: ...

    def finish(self, outcome: str, *, error: BaseException | None = None) -> None: ...


class Tracer(Protocol):
    """Start bounded spans without inheriting any concrete tracing implementation."""

    def start_span(self, name: str, *, attributes: dict[str, object]) -> Span | None: ...

    def start_root_span(self, name: str, *, attributes: dict[str, object]) -> Span | None: ...


__all__ = ["Span", "Tracer"]
