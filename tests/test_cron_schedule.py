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

"""The ``Cron`` collect/generate schedule must be validated at declaration time.

An invalid cron expression or IANA time zone previously passed through the model
unchallenged and only failed later, inside the server runner's ``_collect_due`` or the
device runner's ``_next_delay``. Because the device runner computes the next delay
outside its per-plugin ``try`` block, one invalid schedule stopped collection for every
plugin behind it. See issue #2733.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.plugins import Cron, Interval


def test_cron_accepts_a_valid_expression_and_timezone() -> None:
    cron = Cron(expression="*/5 * * * *", timezone="Asia/Shanghai")
    assert cron.expression == "*/5 * * * *"
    assert cron.timezone == "Asia/Shanghai"


def test_cron_accepts_none_timezone() -> None:
    cron = Cron(expression="0 0 * * *")
    assert cron.timezone is None


def test_cron_rejects_an_invalid_expression() -> None:
    with pytest.raises(ValidationError):
        Cron(expression="every five minutes")


def test_cron_rejects_an_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Cron(expression="* * * * *", timezone="Not/AZone")


def test_interval_is_unaffected() -> None:
    interval = Interval(seconds=60)
    assert interval.seconds == 60
