from powercontext.runtime import SourceCursor, SourceHighWatermark, SourceWindowTrigger


def test_source_window_trigger_selects_a_bounded_window() -> None:
    trigger = SourceWindowTrigger()
    transition = trigger.activate(SourceHighWatermark(sequence=9, limit=3), SourceCursor(sequence=2))

    assert transition.state == SourceCursor(sequence=5)
    assert transition.actions[0].after == 2
    assert transition.actions[0].through == 5


def test_source_window_trigger_is_idle_at_the_high_watermark() -> None:
    trigger = SourceWindowTrigger()
    state = SourceCursor(sequence=4)

    assert trigger.activate(SourceHighWatermark(sequence=4, limit=10), state).actions == ()
