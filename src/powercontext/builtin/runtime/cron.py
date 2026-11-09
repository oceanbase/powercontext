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

"""Small five-field cron schedule used by durable work discovery."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Feb 29 on a fixed weekday recurs at most every 40 years, and 400 years is a full
# Gregorian cycle, so this horizon reaches every occurrence a valid expression has.
_SEARCH_HORIZON_YEARS = 400

_MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_WEEKDAY_NAMES = {name: index for index, name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """Validated crontab expression with deterministic UTC next-run calculation."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    timezone: ZoneInfo
    last_day: bool = False

    @classmethod
    def parse(cls, expression: str, timezone: str, /) -> CronSchedule:
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron expression must contain five fields")  # noqa: TRY003
        days, last_day = _parse_day_field(fields[2])
        return cls(
            minutes=_parse_field(fields[0], minimum=0, maximum=59),
            hours=_parse_field(fields[1], minimum=0, maximum=23),
            days=days,
            months=_parse_field(fields[3], minimum=1, maximum=12, names=_MONTH_NAMES),
            weekdays=_parse_field(fields[4], minimum=0, maximum=6, names=_WEEKDAY_NAMES),
            timezone=ZoneInfo(timezone),
            last_day=last_day,
        )

    def next_after(self, value: datetime, /) -> datetime:
        """Return the first matching instant after ``value`` as UTC-naive time."""

        utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        # A repeated fall-back hour maps a later wall time to an earlier instant, so
        # the earliest future occurrence can belong to the previous local date.
        first_date = utc.astimezone(self.timezone).date() - timedelta(days=1)
        for candidate_date in self._candidate_dates(first_date):
            # Order the day's occurrences by real instant, not by wall clock: on a
            # transition day 01:00 fold=1 (06:00Z) precedes 01:16 fold=0 (05:16Z).
            occurrences = sorted(
                (
                    instant
                    for hour in self.hours
                    for minute in self.minutes
                    for instant in _local_occurrences(
                        datetime.combine(candidate_date, time(hour, minute)), self.timezone
                    )
                ),
                key=lambda instant: instant.astimezone(UTC),
            )
            for candidate in occurrences:
                if candidate.astimezone(UTC) > utc:
                    return candidate.astimezone(UTC).replace(tzinfo=None)
        raise ValueError("cron expression has no occurrence in the next four hundred years")  # noqa: TRY003

    def _candidate_dates(self, first_date: date) -> Iterator[date]:
        """Yield matching dates across a full Gregorian cycle, in ascending order.

        Scanning by month keeps the leap-day case (``0 2 29 2 mon``, next hit in
        2044) inside the horizon instead of failing after an arbitrary cutoff.
        """

        for year in range(first_date.year, first_date.year + _SEARCH_HORIZON_YEARS):
            for month in sorted(self.months):
                month_last_day = monthrange(year, month)[1]
                candidates = {day for day in self.days if day <= month_last_day}
                if self.last_day:
                    candidates.add(month_last_day)
                for day in sorted(candidates):
                    candidate = date(year, month, day)
                    if candidate >= first_date and candidate.weekday() in self.weekdays:
                        yield candidate


def _parse_day_field(value: str) -> tuple[frozenset[int], bool]:
    """Split the day-of-month field into numbered days and the ``last`` marker.

    ``last`` is the APScheduler spelling for the final day of the month, which
    single-node Profile schedules were configured with before durable discovery.
    Rejecting it would fail an existing configuration merely for being upgraded.
    """

    numbered: list[str] = []
    last_day = False
    for part in value.casefold().split(","):
        if part == "last":
            last_day = True
        else:
            numbered.append(part)
    days = _parse_field(",".join(numbered), minimum=1, maximum=31) if numbered else frozenset()
    if not days and not last_day:
        raise ValueError("cron day field cannot be empty")  # noqa: TRY003
    return frozenset(days), last_day


def _parse_field(
    value: str,
    *,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None = None,
) -> frozenset[int]:
    selected: set[int] = set()
    for part in value.casefold().split(","):
        if not part:
            raise ValueError("cron fields cannot contain empty items")  # noqa: TRY003
        base, separator, step_text = part.partition("/")
        if separator and (not step_text.isdecimal() or int(step_text) < 1):
            raise ValueError("cron step must be a positive integer")  # noqa: TRY003
        step = int(step_text) if separator else 1
        if base == "*":
            first, last = minimum, maximum
        elif "-" in base:
            first_text, last_text = base.split("-", 1)
            first = _field_value(first_text, names)
            last = _field_value(last_text, names)
        else:
            first = _field_value(base, names)
            last = maximum if separator else first
        if first < minimum or last > maximum or first > last:
            raise ValueError("cron field is outside its valid range")  # noqa: TRY003
        selected.update(range(first, last + 1, step))
    return frozenset(selected)


def _field_value(value: str, names: dict[str, int] | None) -> int:
    if names is not None and value in names:
        return names[value]
    if not value.isdecimal():
        raise ValueError("cron field must be numeric or a supported name")  # noqa: TRY003
    return int(value)


def _local_occurrences(naive: datetime, timezone: ZoneInfo) -> Iterator[datetime]:
    """Yield every real instant of a local wall time, earliest UTC instant first.

    Ambiguous local times (DST fall-back repeated hour) occur twice; nonexistent
    local times (spring-forward gap) never occur and yield nothing.
    """

    first = naive.replace(tzinfo=timezone)
    if not _is_local_time(first):
        return
    yield first
    second = naive.replace(tzinfo=timezone, fold=1)
    if second.astimezone(UTC) != first.astimezone(UTC):
        yield second


def _is_local_time(value: datetime) -> bool:
    round_trip = value.astimezone(UTC).astimezone(value.tzinfo)
    return round_trip.replace(tzinfo=None) == value.replace(tzinfo=None)


__all__ = ["CronSchedule"]
