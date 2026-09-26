"""Finding issuers whose denial behaviour is genuinely unusual.

Uses the modified z-score, built on the median and the median absolute
deviation (MAD), rather than the ordinary z-score built on mean and standard
deviation.

The reason is *masking*: outliers inflate the standard deviation, which raises
the very threshold meant to catch them. Measured on synthetic denial-rate
distributions (see tests/test_outliers.py):

    30 ordinary issuers + 2 extreme (55%, 61%)   MAD 2/2   ordinary z 2/2
    30 ordinary issuers + 6 elevated (45-58%)    MAD 6/6   ordinary z 0/6
    30 ordinary issuers + 3 mild (22-25%)        MAD 3/3   ordinary z 0/3

With one or two extreme values both methods work. The divergence appears where
this data actually lives: several issuers elevated together, or elevated only
moderately. Six issuers around 50% raise the standard deviation enough that the
ordinary method flags none of them. The median and MAD are unmoved by extreme
values, so the threshold stays where the bulk of the distribution is.

    modified_z = 0.6745 * (x - median) / MAD

0.6745 is the 0.75 quantile of the standard normal, rescaling MAD so a modified
z-score is comparable to an ordinary one on normal data. The conventional flag
is |modified_z| > 3.5.

The module also holds `exceeds_by_more_than_chance`, used for the second signal
in app/analysis.py. An overturn rate is a proportion measured over however many
appeals a member population happened to file, which ranges from 34 to 2,220 in
this dataset. "Above the market median" means something different at each end of
that range, so the comparison is a one-sided test rather than a bare `>`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SCALE = 0.6745

# One-sided normal critical value at p < 0.05. An overturn rate has to clear the
# market median by this many standard errors before it counts as corroboration.
SIGNIFICANCE_Z = 1.645

# The normal approximation to the binomial needs a few expected successes and
# failures on each side before it can be trusted. Below this, no claim is made.
MIN_EXPECTED_COUNT = 5


@dataclass(frozen=True)
class Outlier:
    label: str
    value: float
    modified_z: float
    median: float

    @property
    def direction(self) -> str:
        return "above" if self.modified_z > 0 else "below"


def modified_z_scores(values: list[float]) -> np.ndarray:
    """Modified z-score per value. All zeros when the data has no spread."""
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return array
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    if mad == 0:
        # Every value identical, or so tightly clustered that MAD collapses.
        # Returning zeros says "nothing is unusual here", which is correct, and
        # avoids dividing by zero to produce spurious infinite outliers.
        return np.zeros_like(array)
    return SCALE * (array - median) / mad


def standard_errors_above(
    rate: float | None,
    baseline: float | None,
    sample_size: int | None,
) -> float | None:
    """How many standard errors `rate` sits above `baseline`, or None.

    One-sided comparison of a proportion against a fixed baseline, using the
    normal approximation to the binomial.

    None means the comparison could not be made at all: no data, an empty
    sample, a degenerate baseline, or too few expected counts for the
    approximation to hold. That is deliberately not 0.0 - a comparison that
    could not be made must not be mistaken for one that came out even.
    """
    if rate is None or baseline is None or not sample_size:
        return None
    if not 0.0 < baseline < 1.0:
        # No spread to test against; every deviation would look infinite.
        return None
    if min(baseline, 1.0 - baseline) * sample_size < MIN_EXPECTED_COUNT:
        return None

    standard_error = math.sqrt(baseline * (1.0 - baseline) / sample_size)
    return (rate - baseline) / standard_error


def exceeds_by_more_than_chance(
    rate: float | None,
    baseline: float | None,
    sample_size: int | None,
    z_threshold: float = SIGNIFICANCE_Z,
) -> bool:
    """Is `rate` above `baseline` by more than sampling noise would explain?

    The question this answers, concretely: one insurer overturned 71% of 48
    appeals, another 47% of 342. Both are above the market median of 42.5%, but
    only one of those gaps is too large to be chance. Treating them as the same
    evidence would let a handful of appeals carry a finding.

    Returns False - not an exception - whenever the test cannot be run at all.
    A test that could not be run is not evidence, and the caller should not be
    able to mistake it for a passing one.
    """
    score = standard_errors_above(rate, baseline, sample_size)
    return score is not None and score > z_threshold


def find_outliers(
    labels: list[str], values: list[float], threshold: float = 3.5
) -> list[Outlier]:
    """Flag values whose modified z-score exceeds the threshold, worst first."""
    if len(labels) != len(values):
        raise ValueError("labels and values must be the same length")
    if not values:
        return []

    scores = modified_z_scores(values)
    median = float(np.median(np.asarray(values, dtype=float)))

    flagged = [
        Outlier(label=label, value=float(value), modified_z=float(score), median=median)
        for label, value, score in zip(labels, values, scores, strict=True)
        if abs(score) > threshold
    ]
    return sorted(flagged, key=lambda o: abs(o.modified_z), reverse=True)
