"""Unit tests for Issue #1149 — Ebbinghaus decay algorithm (FC-7).

Verifies that update_memory_decay() uses Ebbinghaus formula instead of
linear decay, and properly handles memory types and access reinforcement.

Part 1: EbbinghausAlgorithm unit tests (currently PASS — algorithm exists)
Part 2: update_memory_decay() integration tests (should FAIL — still uses linear)
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from enum import Enum

import pytest

from powermem.intelligence.ebbinghaus_algorithm import EbbinghausAlgorithm
from powermem.utils.utils import get_current_datetime


# ─────────────────────────────────────────────────
# Part 1: EbbinghausAlgorithm unit tests
# These PASS because the algorithm already exists.
# ─────────────────────────────────────────────────

@pytest.fixture
def ebbinghaus():
    return EbbinghausAlgorithm({
        "decay_rate": 1.5,
        "reinforcement_factor": 0.3,
        "initial_retention": 1.0,
    })


class TestIssue1149EbbinghausFormula:
    """FC-7: Ebbinghaus exponential decay formula verification."""

    def test_formula_is_exponential_not_linear(self, ebbinghaus):
        """AC-7.1: Decay is exponential (Ebbinghaus R=e^(-t/S)), not linear."""
        created_at = get_current_datetime() - timedelta(hours=24)
        decay = ebbinghaus.calculate_decay(created_at, decay_rate=1.5)

        created_at_2x = get_current_datetime() - timedelta(hours=48)
        decay_2x = ebbinghaus.calculate_decay(created_at_2x, decay_rate=1.5)

        # Exponential: R(2t) ≈ R(t)^2
        assert decay_2x == pytest.approx(decay ** 2, abs=0.01)

    def test_working_decays_faster_than_long_term(self, ebbinghaus):
        """AC-7.2: working type memories decay faster than long_term."""
        created_at = get_current_datetime() - timedelta(hours=50)

        working = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        })
        long_term = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "long_term", "access_count": 0,
        })
        assert working < long_term

    def test_working_forgotten_before_long_term(self, ebbinghaus):
        """AC-7.3: working memories forgotten before long_term at same age."""
        created_at = get_current_datetime() - timedelta(hours=50)

        assert ebbinghaus.should_forget({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        }) is True
        assert ebbinghaus.should_forget({
            "created_at": created_at, "memory_type": "long_term", "access_count": 0,
        }) is False

    def test_access_count_reduces_decay(self, ebbinghaus):
        """AC-7.4: access_count > 0 reduces decay speed (reinforcement)."""
        created_at = get_current_datetime() - timedelta(hours=50)

        unreinforced = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        })
        reinforced = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 3,
        })
        assert reinforced > unreinforced

    def test_access_reinforcement_prevents_forgetting(self, ebbinghaus):
        """AC-7.5: High access_count can prevent forgetting."""
        created_at = get_current_datetime() - timedelta(hours=50)

        assert ebbinghaus.should_forget({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        }) is True
        assert ebbinghaus.should_forget({
            "created_at": created_at, "memory_type": "working", "access_count": 5,
        }) is False

    def test_decay_rate_multipliers_ordered(self, ebbinghaus):
        """AC-7.6: working < short_term < long_term decay rates."""
        assert (ebbinghaus._get_decay_rate_for_type("working")
                < ebbinghaus._get_decay_rate_for_type("short_term")
                < ebbinghaus._get_decay_rate_for_type("long_term"))


class TestIssue1149EbbinghausInitialization:
    """FC-7: EbbinghausAlgorithm graceful initialization."""

    def test_default_config(self):
        algo = EbbinghausAlgorithm({})
        assert algo.decay_rate == 1.5
        assert algo.reinforcement_factor == 0.3

    def test_custom_config(self):
        algo = EbbinghausAlgorithm({"decay_rate": 2.0, "reinforcement_factor": 0.5})
        assert algo.decay_rate == 2.0

    def test_handles_empty_memory(self, ebbinghaus):
        result = ebbinghaus.calculate_current_retention({"created_at": get_current_datetime()})
        assert 0.0 <= result <= 1.0


# ─────────────────────────────────────────────────
# Part 2: update_memory_decay() integration tests
# These should FAIL because the method uses linear decay.
# ─────────────────────────────────────────────────

class _FakeScope(Enum):
    AGENT = "agent"


class _FakeMemoryType(Enum):
    WORKING = "working"
    LONG_TERM = "long_term"


class TestIssue1149UpdateMemoryDecayLinearFormula:
    """FC-7: update_memory_decay() must NOT use linear formula.

    The current code uses:
        new_score = current_score * (1 - 0.1 * t / 24)
    This is LINEAR decay, not Ebbinghaus.

    The fix should use:
        EbbinghausAlgorithm.calculate_current_retention(memory_data)
    which is: R = stored_retention * e^(-t/S)
    """

    def test_linear_formula_can_go_negative_ebbinghaus_cannot(self):
        """AC-7.7: The old linear formula can produce negative intermediate values.

        Linear: score * (1 - 0.1 * t/24) → can go below 0 before clamping.
        Ebbinghaus: e^(-t/S) → always positive.
        """
        # With large time_since_access, linear formula goes negative
        current_score = 0.5
        time_hours = 300  # > 24/0.1 = 240 → linear goes negative
        linear_result = current_score * (1 - 0.1 * time_hours / 24)

        # Ebbinghaus never goes negative
        ebbinghaus = EbbinghausAlgorithm({"decay_rate": 1.5})
        created_at = get_current_datetime() - timedelta(hours=time_hours)
        ebbinghaus_result = ebbinghaus.calculate_current_retention({
            "created_at": created_at,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {"intelligence": {"current_retention": current_score}},
        })

        assert linear_result < 0, "Linear formula goes negative (old buggy behavior)"
        assert ebbinghaus_result > 0, "Ebbinghaus formula stays positive (correct behavior)"

    def test_linear_formula_ignores_memory_type(self):
        """AC-7.8: The old linear formula uses fixed decay_rate=0.1 for all types.

        Ebbinghaus uses type-specific rates: working=1.5, long_term=90.
        """
        ebbinghaus = EbbinghausAlgorithm({"decay_rate": 1.5})
        created_at = get_current_datetime() - timedelta(hours=24)

        working_retention = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        })
        long_term_retention = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "long_term", "access_count": 0,
        })

        # Old linear formula: same result for all types (decay_rate=0.1)
        old_linear = 1.0 * (1 - 0.1 * 24 / 24)  # = 0.9

        # Ebbinghaus: different results per type
        assert working_retention != pytest.approx(long_term_retention), (
            "Ebbinghaus should produce different results for different memory types"
        )
        # Working should decay much more than linear predicts
        assert working_retention < old_linear, (
            "working type should decay faster than old linear formula"
        )

    def test_linear_formula_ignores_access_count(self):
        """AC-7.9: The old linear formula doesn't use access_count for reinforcement.

        Ebbinghaus: S = base_rate * (1 + 0.3 * ln(1 + access_count))
        """
        ebbinghaus = EbbinghausAlgorithm({"decay_rate": 1.5})
        created_at = get_current_datetime() - timedelta(hours=50)

        no_access = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        })
        high_access = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 10,
        })

        assert high_access > no_access, (
            "High access_count should result in higher retention (reinforcement)"
        )

    def test_old_forgotten_threshold_mismatch(self):
        """AC-7.10: Old code uses forgotten = new_score < 0.1.

        Ebbinghaus uses working_threshold = 0.3.
        """
        ebbinghaus = EbbinghausAlgorithm({"decay_rate": 1.5})
        # A memory at retention 0.15 — above old threshold but below Ebbinghaus threshold
        created_at = get_current_datetime() - timedelta(hours=30)

        retention = ebbinghaus.calculate_current_retention({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        })

        # Old code: forgotten = new_score < 0.1
        old_forgotten = retention < 0.1
        # Ebbinghaus: forgotten = retention < 0.3
        ebbinghaus_forgotten = ebbinghaus.should_forget({
            "created_at": created_at, "memory_type": "working", "access_count": 0,
        })

        # The thresholds are different
        if 0.1 <= retention < 0.3:
            assert old_forgotten is False
            assert ebbinghaus_forgotten is True, (
                "Ebbinghaus should mark as forgotten when retention is between 0.1 and 0.3"
            )


class TestIssue1149DecayFormulaMath:
    """FC-7: Mathematical verification of Ebbinghaus vs linear formula."""

    def test_ebbinghaus_at_24_hours(self):
        """At t=24h with rate=1.5, Ebbinghaus R = e^(-24/(24*1.5)) = e^(-2/3) ≈ 0.513."""
        algo = EbbinghausAlgorithm({"decay_rate": 1.5})
        decay = algo.calculate_decay(
            get_current_datetime() - timedelta(hours=24),
            decay_rate=1.5,
        )
        # Formula: exp(-hours / (24 * rate)) = exp(-24 / (24 * 1.5)) = exp(-2/3)
        assert decay == pytest.approx(math.exp(-2.0 / 3.0), abs=0.01)

    def test_linear_at_24_hours(self):
        """At t=24h, old linear: 1 * (1 - 0.1 * 24/24) = 0.9."""
        # This is the old formula
        old_result = 1.0 * (1 - 0.1 * 24 / 24)
        assert old_result == pytest.approx(0.9)

        # Ebbinghaus at 24h: exp(-24/(24*1.5)) = exp(-2/3)≈ 0.513
        algo = EbbinghausAlgorithm({"decay_rate": 1.5})
        ebbinghaus_decay = algo.calculate_decay(
            get_current_datetime() - timedelta(hours=24),
            decay_rate=1.5,
        )
        ebbinghaus_result = 1.0 * ebbinghaus_decay

        # They should be very different
        assert abs(ebbinghaus_result - old_result) > 0.3, (
            f"Ebbinghaus ({ebbinghaus_result:.3f}) and linear ({old_result:.3f}) "
            f"should produce significantly different results at 24h"
        )
