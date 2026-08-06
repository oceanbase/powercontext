from __future__ import annotations

from powercontext.builtin.inference import TokenEstimatorProfile, character_token_estimator


def test_character_estimator_is_deterministic_for_ascii_and_non_ascii_text() -> None:
    estimator = character_token_estimator()

    assert estimator.profile == TokenEstimatorProfile(estimator_id="character:weighted", version="1")
    assert estimator.estimate("") == 0
    assert estimator.estimate("abcd") == 1
    assert estimator.estimate("abcde") == 2
    assert estimator.estimate("上下文") == 3
    assert estimator.estimate("ab上下") == 3
