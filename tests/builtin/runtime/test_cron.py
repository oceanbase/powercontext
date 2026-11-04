# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression tests for cron scheduling across DST transitions."""

from datetime import datetime

from powercontext.builtin.runtime.cron import CronSchedule


def test_next_after_stays_in_the_future_during_the_repeated_hour() -> None:
    """The fall-back repeated hour must never yield a UTC instant before the input."""

    schedule = CronSchedule.parse("* * * * *", "America/New_York")
    result = schedule.next_after(datetime(2026, 11, 1, 6, 15))  # noqa: DTZ001 - naive on purpose
    assert result > datetime(2026, 11, 1, 6, 15)  # noqa: DTZ001 - naive on purpose
    # 06:15 UTC is 01:15 EDT->EST territory; the next minute is 01:16 EST = 06:16 UTC.
    assert result == datetime(2026, 11, 1, 6, 16)  # noqa: DTZ001 - naive on purpose


def test_next_after_returns_the_first_repeated_hour_occurrence() -> None:
    """The earlier occurrence inside the repeated hour must not be skipped."""

    schedule = CronSchedule.parse("* * * * *", "America/New_York")
    # 05:15 UTC is 01:15 EDT (fold=0); 01:16 EDT = 05:16 UTC comes first.
    assert schedule.next_after(datetime(2026, 11, 1, 5, 15)) == datetime(2026, 11, 1, 5, 16)  # noqa: DTZ001 - naive on purpose
    # 05:00 UTC is 01:00 EDT; the earliest future minute is 01:01 EDT = 05:01 UTC,
    # not 02:00 EST = 07:00 UTC and not 01:01 EST = 06:01 UTC.
    assert schedule.next_after(datetime(2026, 11, 1, 5, 0)) == datetime(2026, 11, 1, 5, 1)  # noqa: DTZ001 - naive on purpose


def test_next_after_walks_every_minute_through_the_repeated_hour() -> None:
    """Minute-by-minute scans advance one real minute per step across the transition."""

    schedule = CronSchedule.parse("* * * * *", "America/New_York")
    # 05:59 UTC is 01:59 EDT, the last minute before the fall-back.
    previous = schedule.next_after(datetime(2026, 11, 1, 5, 59))  # noqa: DTZ001 - naive on purpose
    assert previous == datetime(2026, 11, 1, 6, 0)  # noqa: DTZ001 - naive on purpose
    for expected in (datetime(2026, 11, 1, 6, minute) for minute in range(1, 60)):  # noqa: DTZ001 - naive on purpose
        result = schedule.next_after(previous)
        assert result == expected
        previous = result
    # The first minute after the repeated hour is 02:00 EST = 07:00 UTC.
    assert schedule.next_after(previous) == datetime(2026, 11, 1, 7, 0)  # noqa: DTZ001 - naive on purpose


def test_next_after_emits_both_occurrences_of_an_ambiguous_wall_time() -> None:
    """01:30 happens twice on the fall-back day and each occurrence is returned once."""

    schedule = CronSchedule.parse("30 1 * * *", "America/New_York")
    first = schedule.next_after(datetime(2026, 11, 1, 5, 0))  # noqa: DTZ001 - naive on purpose
    assert first == datetime(2026, 11, 1, 5, 30)  # 01:30 EDT  # noqa: DTZ001 - naive on purpose
    second = schedule.next_after(first)
    assert second == datetime(2026, 11, 1, 6, 30)  # 01:30 EST, the repeated hour  # noqa: DTZ001 - naive on purpose
    third = schedule.next_after(second)
    assert third == datetime(2026, 11, 2, 6, 30)  # next day, 01:30 EST  # noqa: DTZ001 - naive on purpose


def test_next_after_skips_nonexistent_wall_times_in_the_spring_forward_gap() -> None:
    """02:30 does not exist on the spring-forward day; the next day's match is used."""

    schedule = CronSchedule.parse("30 2 * * *", "America/New_York")
    result = schedule.next_after(datetime(2026, 3, 8, 6, 0))  # noqa: DTZ001 - naive on purpose
    # 02:30 EST is skipped by the gap on 2026-03-08; the next 02:30 is on 03-09 (EDT, 06:30 UTC).
    assert result == datetime(2026, 3, 9, 6, 30)  # noqa: DTZ001 - naive on purpose


def test_next_after_is_monotonic_under_repeated_feedback() -> None:
    """Feeding a returned instant back must always strictly advance."""

    schedule = CronSchedule.parse("*/15 * * * *", "America/New_York")
    current = datetime(2026, 11, 1, 5, 0)  # noqa: DTZ001 - naive on purpose
    for _ in range(24):
        following = schedule.next_after(current)
        assert following > current
        current = following


def test_next_after_ordinary_days_are_unchanged() -> None:
    """Non-transition days keep the documented UTC-naive semantics."""

    schedule = CronSchedule.parse("30 9 * * *", "America/New_York")
    # 2026-06-15: 09:30 EDT = 13:30 UTC.
    assert schedule.next_after(datetime(2026, 6, 15, 12, 0)) == datetime(2026, 6, 15, 13, 30)  # noqa: DTZ001 - naive on purpose
    assert schedule.next_after(datetime(2026, 6, 15, 13, 30)) == datetime(2026, 6, 16, 13, 30)  # noqa: DTZ001 - naive on purpose
