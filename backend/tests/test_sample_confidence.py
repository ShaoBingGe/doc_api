"""Sample-count → iteration-confidence tiers (overfitting guard)."""

from app.ocr_optimizer.service.customer_iteration import (
    HIGH_CONFIDENCE_SAMPLES,
    LOW_CONFIDENCE_SAMPLES,
    MIN_SAMPLES_FOR_ITERATION,
    sample_confidence,
)


def test_below_min_cannot_iterate():
    c = sample_confidence(MIN_SAMPLES_FOR_ITERATION - 1)
    assert c["level"] == "insufficient"
    assert c["can_iterate"] is False
    assert str(MIN_SAMPLES_FOR_ITERATION) in c["message"]


def test_at_min_is_low_confidence_but_can_iterate():
    c = sample_confidence(MIN_SAMPLES_FOR_ITERATION)
    assert c["level"] == "low"
    assert c["can_iterate"] is True
    assert c["recommended"] == LOW_CONFIDENCE_SAMPLES


def test_medium_tier():
    c = sample_confidence(LOW_CONFIDENCE_SAMPLES)
    assert c["level"] == "medium"
    assert c["can_iterate"] is True


def test_high_tier():
    c = sample_confidence(HIGH_CONFIDENCE_SAMPLES)
    assert c["level"] == "high"
    assert c["can_iterate"] is True


def test_monotonic_levels():
    order = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    seq = [order[sample_confidence(n)["level"]] for n in range(0, 15)]
    assert seq == sorted(seq)  # never regresses as samples grow
