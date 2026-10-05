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

"""Example input checks for Laya's choice sequence builder."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from powercontext.builtin.inference import InferenceConfigurationError


@dataclass(frozen=True, slots=True)
class LayaInputBudget:
    """The tokenizer and sequence limits of the checkpoint serving the request.

    ``tokenize`` must return token IDs with ``add_special_tokens=False`` and no
    truncation. Supply ``max_length`` and ``head_max_length`` from the checkpoint's
    ``max_len`` and ``head_max_len`` configuration. A different server checkpoint
    or tokenizer invalidates this preflight; it cannot discover remote settings.
    """

    tokenize: Callable[[str], Sequence[int]] = field(repr=False)
    max_length: int
    head_max_length: int
    mask_token: str

    def __post_init__(self) -> None:
        for name in ("max_length", "head_max_length"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise InferenceConfigurationError(f"Laya {name} must be a positive integer")  # noqa: TRY003
        if not callable(self.tokenize):
            raise InferenceConfigurationError("Laya tokenize must be callable")  # noqa: TRY003
        if not isinstance(self.mask_token, str) or not self.mask_token.strip():
            raise InferenceConfigurationError("Laya mask_token must be nonempty")  # noqa: TRY003

    def validate(self, state: str, instructions: str, criteria: Mapping[str, str]) -> None:
        """Require every instruction, option, and state token to survive encoding.

        Mirrors Laya's choice rendering and three clipping boundaries: individual
        option text, the shared question head, and the full sequence. Content is
        never shortened locally or included in an error message.
        """

        if not criteria:
            raise InferenceConfigurationError("Laya choices must not be empty")  # noqa: TRY003
        option_lengths = [
            self._token_count(" " + (f"{key}: {description}" if description else key)) + 1
            for key, description in criteria.items()
        ]
        if any(length > 49 for length in option_lengths):
            raise InferenceConfigurationError("Laya choice text exceeds the 48-token option limit")  # noqa: TRY003

        option_budget = self.head_max_length - sum(option_lengths)
        if option_budget < 16:
            per_option = max(4, (self.head_max_length - 16) // len(option_lengths))
            if any(length > per_option for length in option_lengths):
                raise InferenceConfigurationError("Laya choices exceed the checkpoint head budget")  # noqa: TRY003

        head_length = self._token_count("choice question: " + instructions)
        if head_length > max(8, option_budget):
            raise InferenceConfigurationError("Laya instructions exceed the checkpoint head budget")  # noqa: TRY003

        # CLS, two head separators, and the final state separator occupy four IDs.
        sequence_length = head_length + sum(option_lengths) + self._token_count(state) + 4
        if sequence_length > self.max_length:
            raise InferenceConfigurationError("Laya input exceeds the checkpoint sequence budget")  # noqa: TRY003

    def _token_count(self, value: str) -> int:
        if self.mask_token in value:
            raise InferenceConfigurationError(  # noqa: TRY003
                "Laya input contains a mask token that the server would replace"
            )
        try:
            return len(self.tokenize(value))
        except Exception:
            raise InferenceConfigurationError("Laya could not tokenize the decision input") from None  # noqa: TRY003


__all__ = ["LayaInputBudget"]
