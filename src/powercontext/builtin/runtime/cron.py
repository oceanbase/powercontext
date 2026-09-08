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

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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

    @classmethod
    def parse(cls, expression: str, timezone: str, /) -> CronSchedule:
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron expression must contain five fields")  # noqa: TRY003
        return cls(
            minutes=_parse_field(fields[0], minimum=0, maximum=59),
            hours=_parse_field(fields[1], minimum=0, maximum=23),
            days=_parse_field(fields[2], minimum=1, maximum=31),
            months=_parse_field(fields[3], minimum=1, maximum=12, names=_MONTH_NAMES),
            weekdays=_parse_field(fields[4], minimum=0, maximum=6, names=_WEEKDAY_NAMES),
            timezone=ZoneInfo(timezone),
        )

    def next_after(self, value: datetime, /) -> datetime:
        """Return the first matching instant after ``value`` as UTC-naive time."""

        utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        start = (utc.astimezone(self.timezone) + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for offset in range(366 * 8):
            candidate_date = start.date() + timedelta(days=offset)
            if not self._matches_date(candidate_date):
                continue
            for hour in sorted(self.hours):
                for minute in sorted(self.minutes):
                    candidate = datetime.combine(candidate_date, time(hour, minute), self.timezone)
                    if candidate < start or not _is_local_time(candidate):
                        continue
                    return candidate.astimezone(UTC).replace(tzinfo=None)
        raise ValueError("cron expression has no occurrence in the next eight years")  # noqa: TRY003

    def _matches_date(self, value: date) -> bool:
        return value.month in self.months and value.day in self.days and value.weekday() in self.weekdays


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


def _is_local_time(value: datetime) -> bool:
    round_trip = value.astimezone(UTC).astimezone(value.tzinfo)
    return round_trip.replace(tzinfo=None) == value.replace(tzinfo=None)


__all__ = ["CronSchedule"]
