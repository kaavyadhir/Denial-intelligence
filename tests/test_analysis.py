"""Finding classification.

The rules decide what gets reported and on what evidence, so they are tested
directly rather than inferred from the output of a full run.

Appeal counts in these cases are chosen to sit either side of the significance
test in app/outliers.py, so that a test failing here points at the rule that
broke rather than at arithmetic.
"""
import pytest

from app.analysis import classify, median

OUTLIER = 3.5
WATCH = 2.0
MARKET_OVERTURN = 0.43

# 1,730 appeals: the real Blue Cross Alabama volume. A four-point gap over this
# many appeals is 3.4 standard errors, comfortably past the test.
MANY_APPEALS = 1730
# 40 appeals: enough for the normal approximation, not enough for a small gap.
FEW_APPEALS = 40


def test_ordinary_issuer_is_not_reported():
    assert classify(0.4, 0.30, MARKET_OVERTURN, OUTLIER, WATCH, MANY_APPEALS) is None


def test_elevated_denials_alone_are_not_enough():
    """z=2.5 is above the watch threshold but its appeals largely stand."""
    assert classify(2.5, 0.20, MARKET_OVERTURN, OUTLIER, WATCH, MANY_APPEALS) is None


def test_elevated_denials_plus_high_overturns_is_the_strongest_finding():
    """Two moderate signals agreeing. This is the case the project exists for."""
    assert classify(
        2.4, 0.47, MARKET_OVERTURN, OUTLIER, WATCH, MANY_APPEALS
    ) == "denials_overturned_on_appeal"


def test_corroboration_can_promote_a_sub_outlier_score():
    """z=2.19 would be discarded by a 3.5 cut. With 47% overturns it is reported.

    Real case: Blue Cross and Blue Shield of Alabama, PY2025, 4.5 million
    claims denied out of 13 million received.
    """
    assert classify(
        2.19, 0.4659, MARKET_OVERTURN, OUTLIER, WATCH, MANY_APPEALS
    ) == "denials_overturned_on_appeal"


def test_a_large_gap_corroborates_even_on_a_small_appeal_sample():
    """71% against a 43% median is too wide to be chance even over 48 appeals.

    Real case: AmeriHealth Caritas North Carolina. The sample is small, so the
    test has to clear a wide bar - and this gap does, at 4.0 standard errors.
    """
    assert classify(
        2.78, 0.7083, MARKET_OVERTURN, OUTLIER, WATCH, 48
    ) == "denials_overturned_on_appeal"


def test_a_small_gap_on_a_small_sample_does_not_corroborate():
    """The same four-point gap that is evidence over 1,730 appeals is not
    evidence over 40. This is the whole reason the test exists."""
    assert classify(2.4, 0.47, MARKET_OVERTURN, OUTLIER, WATCH, FEW_APPEALS) is None


def test_unknown_appeal_volume_cannot_corroborate():
    """An overturn rate with no denominator behind it proves nothing."""
    assert classify(2.4, 0.47, MARKET_OVERTURN, OUTLIER, WATCH, None) is None


def test_extreme_denials_with_no_appeal_data_are_labelled_unverifiable():
    """Not ranked as though we knew whether the denials were justified."""
    assert classify(
        4.75, None, MARKET_OVERTURN, OUTLIER, WATCH, 0
    ) == "extreme_denials_unverifiable"


def test_extreme_denials_whose_appeals_stand_are_separated_out():
    assert classify(
        4.0, 0.10, MARKET_OVERTURN, OUTLIER, WATCH, MANY_APPEALS
    ) == "extreme_denials_upheld"


def test_extreme_denials_are_reported_even_without_corroboration():
    """The outlier threshold does not depend on appeal evidence at all."""
    assert classify(
        4.0, 0.47, MARKET_OVERTURN, OUTLIER, WATCH, FEW_APPEALS
    ) == "extreme_denials_upheld"


def test_missing_appeal_data_below_the_outlier_threshold_is_not_reported():
    """Elevated but unverifiable and not extreme. Nothing can be said."""
    assert classify(2.5, None, MARKET_OVERTURN, OUTLIER, WATCH, 0) is None


def test_no_market_overturn_median_prevents_corroboration():
    """If no peer reports appeals, an overturn rate cannot be called high."""
    assert classify(2.5, 0.60, None, OUTLIER, WATCH, MANY_APPEALS) is None


def test_overturn_rate_exactly_at_the_median_is_not_above_it():
    assert classify(
        2.5, MARKET_OVERTURN, MARKET_OVERTURN, OUTLIER, WATCH, MANY_APPEALS
    ) is None


@pytest.mark.parametrize("values,expected", [
    ([], None),
    ([0.5], 0.5),
    ([0.2, 0.4], pytest.approx(0.3)),
    ([0.1, 0.2, 0.9], 0.2),
    ([0.4, 0.1, 0.3, 0.2], pytest.approx(0.25)),
])
def test_median_handles_odd_even_and_empty(values, expected):
    assert median(values) == expected
