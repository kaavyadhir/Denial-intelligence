"""Outlier detection, including the case that motivated the method choice."""
import numpy as np
import pytest

from app.outliers import (
    exceeds_by_more_than_chance,
    find_outliers,
    modified_z_scores,
)


def ordinary_issuers(n=30, seed=7):
    return list(np.round(np.random.default_rng(seed).normal(0.12, 0.02, n), 4))


def ordinary_z_flags(values, threshold=3.5):
    a = np.asarray(values, dtype=float)
    z = (a - a.mean()) / a.std()
    return {i for i in range(len(a)) if abs(z[i]) > threshold}


def test_empty_input_returns_nothing():
    assert find_outliers([], []) == []


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        find_outliers(["a"], [1.0, 2.0])


def test_identical_values_produce_no_outliers():
    """MAD is zero here. Dividing by it would manufacture infinite outliers."""
    assert find_outliers(["a", "b", "c"], [0.2, 0.2, 0.2]) == []
    assert list(modified_z_scores([0.2, 0.2, 0.2])) == [0.0, 0.0, 0.0]


def test_flags_a_clear_outlier_and_reports_direction():
    values = ordinary_issuers() + [0.58]
    labels = [f"i{k}" for k in range(len(values))]
    found = find_outliers(labels, values)

    assert found and found[0].label == labels[-1]
    assert found[0].direction == "above"
    assert found[0].value == pytest.approx(0.58)


def test_results_are_sorted_worst_first():
    values = ordinary_issuers() + [0.45, 0.70]
    labels = [f"i{k}" for k in range(len(values))]
    found = find_outliers(labels, values)

    scores = [abs(o.modified_z) for o in found]
    assert scores == sorted(scores, reverse=True)


def test_mad_catches_clustered_outliers_that_mask_an_ordinary_z_score():
    """Six issuers elevated together. This is why the method is MAD-based.

    Their own spread inflates the standard deviation enough that an ordinary
    z-score flags none of them - the more outliers there are, the better they
    hide. The median and MAD are unaffected.
    """
    planted = [0.45, 0.47, 0.50, 0.52, 0.55, 0.58]
    values = ordinary_issuers() + planted
    labels = [f"i{k}" for k in range(len(values))]
    planted_indices = set(range(len(values) - len(planted), len(values)))

    mad_found = {labels.index(o.label) for o in find_outliers(labels, values)}
    assert planted_indices <= mad_found, "MAD should catch every planted outlier"
    assert not (planted_indices & ordinary_z_flags(values)), \
        "ordinary z-score is expected to miss these - that is the point"


def test_mad_catches_mild_outliers_an_ordinary_z_score_misses():
    """Twice the median rather than five times. Still worth a reviewer's time."""
    planted = [0.22, 0.24, 0.25]
    values = ordinary_issuers() + planted
    labels = [f"i{k}" for k in range(len(values))]
    planted_indices = set(range(len(values) - len(planted), len(values)))

    mad_found = {labels.index(o.label) for o in find_outliers(labels, values)}
    assert planted_indices <= mad_found
    assert not (planted_indices & ordinary_z_flags(values))


def test_low_outliers_are_flagged_too():
    """An implausibly low denial rate is also a reporting anomaly."""
    values = ordinary_issuers() + [0.0001]
    labels = [f"i{k}" for k in range(len(values))]
    found = find_outliers(labels, values)

    assert found and found[0].direction == "below"


# --- exceeds_by_more_than_chance ------------------------------------------
# The second signal in app/analysis.py. Its job is to stop a small appeal
# sample from carrying a finding, so the tests are mostly about sample size.

MEDIAN = 0.43


def test_a_wide_gap_on_a_large_sample_is_significant():
    assert exceeds_by_more_than_chance(0.47, MEDIAN, 1730)


def test_the_same_gap_on_a_small_sample_is_not():
    """0.47 vs 0.43 is 3.4 standard errors over 1,730 appeals and 0.5 over 40.

    Identical rates, opposite verdicts. This is the behaviour the function
    exists for - a bare `rate > median` comparison cannot distinguish them.
    """
    assert not exceeds_by_more_than_chance(0.47, MEDIAN, 40)


def test_a_large_enough_gap_survives_a_small_sample():
    """Small samples are not disqualified, only held to a wider bar."""
    assert exceeds_by_more_than_chance(0.71, MEDIAN, 48)


def test_a_rate_below_the_baseline_is_never_significant():
    """One-sided. An insurer overturning fewer appeals than its peers is not
    evidence against it, however many appeals that is measured over."""
    assert not exceeds_by_more_than_chance(0.10, MEDIAN, 100_000)


def test_a_rate_equal_to_the_baseline_is_not_above_it():
    assert not exceeds_by_more_than_chance(MEDIAN, MEDIAN, 1730)


@pytest.mark.parametrize("rate,baseline,sample", [
    (None, MEDIAN, 1730),   # issuer reported no overturn rate
    (0.47, None, 1730),     # no peer in the market reported appeals
    (0.47, MEDIAN, None),   # appeals filed not reported
    (0.47, MEDIAN, 0),      # no appeals were filed at all
])
def test_untestable_inputs_return_false_rather_than_raising(rate, baseline, sample):
    """A test that could not be run is not a test that passed.

    Returning False keeps a caller from mistaking missing data for evidence,
    and an exception here would take down a whole market's analysis over one
    issuer's blank cell.
    """
    assert not exceeds_by_more_than_chance(rate, baseline, sample)


@pytest.mark.parametrize("baseline", [0.0, 1.0])
def test_a_degenerate_baseline_is_not_testable(baseline):
    """Zero variance would make every deviation look infinitely significant."""
    assert not exceeds_by_more_than_chance(0.5, baseline, 1730)


def test_too_few_expected_counts_for_the_normal_approximation():
    """10 appeals against a 0.43 baseline expects 4.3 overturns - below the
    conventional minimum of 5, so the approximation is not trusted."""
    assert not exceeds_by_more_than_chance(0.9, MEDIAN, 10)
