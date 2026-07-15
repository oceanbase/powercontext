from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, TypeAlias

from powercontext.sources import Source, SourceMaterialization
from powercontext.triggers import PolicyTransition, Trigger


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSource(Source):
    source_type: ClassVar[str] = "conversation"

    session_id: str


@dataclass(frozen=True, slots=True)
class SessionEnded:
    sources: tuple[ConversationSource, ...]


@dataclass(frozen=True, slots=True)
class FlushRequested:
    pass


@dataclass(frozen=True, slots=True)
class TimerWakeup:
    pass


Signal: TypeAlias = SessionEnded | FlushRequested | TimerWakeup


@dataclass(frozen=True, slots=True)
class PendingSources:
    sources: tuple[ConversationSource, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractMemories:
    sources: tuple[ConversationSource, ...]
    reason: str


class ActivateMemoryExtraction(Trigger[Signal, PendingSources, ExtractMemories]):
    def __init__(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError
        self._threshold = threshold

    def initial_state(self) -> PendingSources:
        return PendingSources()

    def activate(
        self,
        signal: Signal,
        state: PendingSources,
        /,
    ) -> PolicyTransition[PendingSources, ExtractMemories]:
        pending = state.sources
        reason: str | None = None

        if isinstance(signal, SessionEnded):
            pending += signal.sources
            if len(pending) >= self._threshold:
                reason = "threshold"
        elif isinstance(signal, FlushRequested) and pending:
            reason = "flush"
        elif isinstance(signal, TimerWakeup) and pending:
            reason = "timer"

        if reason is None:
            return PolicyTransition(state=PendingSources(pending))

        action = ExtractMemories(sources=pending, reason=reason)
        return PolicyTransition(state=PendingSources(), actions=(action,))


def conversation_source(session_id: str) -> ConversationSource:
    return ConversationSource(
        name=f"{session_id}-snapshot",
        materialization=SourceMaterialization.CAPTURED,
        session_id=session_id,
    )


def memory_trigger(threshold: int) -> Trigger[Signal, PendingSources, ExtractMemories]:
    return ActivateMemoryExtraction(threshold)


def test_threshold_action_contains_every_pending_source() -> None:
    first = conversation_source("session-41")
    second = conversation_source("session-42")
    trigger = memory_trigger(threshold=2)

    first_transition = trigger.activate(SessionEnded((first,)), trigger.initial_state())
    second_transition = trigger.activate(SessionEnded((second,)), first_transition.state)

    assert first_transition == PolicyTransition(state=PendingSources((first,)))
    assert second_transition == PolicyTransition(
        state=PendingSources(),
        actions=(ExtractMemories(sources=(first, second), reason="threshold"),),
    )


def test_flush_activates_a_partial_batch() -> None:
    source = conversation_source("session-42")
    trigger = memory_trigger(threshold=2)

    pending = trigger.activate(SessionEnded((source,)), trigger.initial_state())
    activated = trigger.activate(FlushRequested(), pending.state)

    assert activated == PolicyTransition(
        state=PendingSources(),
        actions=(ExtractMemories(sources=(source,), reason="flush"),),
    )


def test_timer_wakeup_activates_only_when_evidence_is_pending() -> None:
    trigger = memory_trigger(threshold=2)
    source = conversation_source("session-42")

    empty = trigger.activate(TimerWakeup(), trigger.initial_state())
    pending = trigger.activate(SessionEnded((source,)), empty.state)
    activated = trigger.activate(TimerWakeup(), pending.state)

    assert empty == PolicyTransition(state=PendingSources())
    assert pending == PolicyTransition(state=PendingSources((source,)))
    assert activated == PolicyTransition(
        state=PendingSources(),
        actions=(ExtractMemories(sources=(source,), reason="timer"),),
    )
