"""Unit tests for EbbinghausAlgorithm review schedule consistency."""

from datetime import datetime, timedelta

import pytest

from powermem.intelligence.ebbinghaus_algorithm import EbbinghausAlgorithm


@pytest.fixture
def algo():
    return EbbinghausAlgorithm({})


@pytest.fixture
def anchor():
    return datetime(2026, 5, 23, 12, 0, 0)


def test_generate_and_get_review_schedule_match(algo, anchor):
    importance = 0.8
    private = algo._generate_review_schedule(importance, anchor)
    public = algo.get_review_schedule(
        {"importance_score": importance, "created_at": anchor},
        prefer_stored=False,
    )
    assert private == public


def test_review_schedule_respects_min_hours(algo, anchor):
    aggressive = EbbinghausAlgorithm(
        {"review_adjustment_factor": 1.0, "review_interval_min_hours": 0.5}
    )
    schedule = aggressive._generate_review_schedule(1.0, anchor)
    first_delta = schedule[0] - anchor
    assert first_delta == timedelta(hours=0.5)


def test_custom_adjustment_factor_changes_intervals(algo, anchor):
    mild = EbbinghausAlgorithm({"review_adjustment_factor": 0.1})
    strong = EbbinghausAlgorithm({"review_adjustment_factor": 0.5})
    mild_first = mild._generate_review_schedule(0.8, anchor)[0]
    strong_first = strong._generate_review_schedule(0.8, anchor)[0]
    assert strong_first < mild_first


def test_get_review_schedule_reads_metadata_fields(algo, anchor):
    memory = {
        "metadata": {
            "importance_score": 0.7,
            "created_at": anchor.isoformat(),
        }
    }
    direct = algo._generate_review_schedule(0.7, anchor)
    from_meta = algo.get_review_schedule(memory, prefer_stored=False)
    assert direct == from_meta


def test_prefer_stored_returns_persisted_schedule(algo, anchor):
    stored_times = [
        anchor + timedelta(hours=1),
        anchor + timedelta(hours=10),
    ]
    memory = {
        "metadata": {
            "intelligence": {
                "review_schedule": [t.isoformat() for t in stored_times],
            },
            "importance_score": 0.8,
            "created_at": anchor.isoformat(),
        }
    }
    recomputed = algo.get_review_schedule(memory, prefer_stored=False)
    assert algo.get_review_schedule(memory, prefer_stored=True) == stored_times
    assert algo.get_review_schedule(memory, prefer_stored=True) != recomputed


def test_prefer_stored_falls_back_when_missing(algo, anchor):
    memory = {"importance_score": 0.6, "created_at": anchor}
    assert algo.get_review_schedule(memory, prefer_stored=True) == (
        algo.get_review_schedule(memory, prefer_stored=False)
    )


def test_parse_stored_review_schedule_top_level_intelligence(algo, anchor):
    t = anchor + timedelta(hours=3)
    memory = {"intelligence": {"review_schedule": [t.isoformat()]}}
    assert algo._parse_stored_review_schedule(memory) == [t]


def test_adjust_review_intervals_formula(algo):
    intervals = algo._adjust_review_intervals(0.8)
    assert intervals[0] == pytest.approx(0.76, rel=1e-6)
    assert intervals[1] == pytest.approx(4.56, rel=1e-6)
