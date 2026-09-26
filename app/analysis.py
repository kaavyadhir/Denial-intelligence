"""Turn metrics into findings.

The split between this module and `sql/metrics.sql` is deliberate. SQL computes
every *rate*; this module decides which rates are unusual and how concerning the
combination is. Neither step involves a language model - by the time the model
sees anything, every number is fixed.

The central idea: a denial rate on its own is a weak signal. An insurer denying
more than its peers may simply be adjudicating strictly and correctly. But an
insurer that denies more than its peers *and* reverses a large share of those
denials when a member appeals was denying claims it should have paid. The appeal
outcome is the closest thing this dataset has to a verdict on whether the
denials were justified.

That is why there are two thresholds rather than one. Measured on PY2025 data,
a single modified-z cut at 3.5 on denial rate surfaced two small issuers with no
appeal data at all, and discarded every issuer where the appeal record actually
showed the denials being overturned - including one reversing 71% of them. Two
moderate signals that corroborate each other are stronger evidence than one
extreme signal in isolation.

The second signal has to earn the word "corroborating". Appeal volumes in this
dataset run from 34 to 2,220, so an overturn rate a point above the market
median can be solid evidence or pure noise depending entirely on how many
appeals it was measured over. The rule therefore asks whether the gap survives a
one-sided significance test, not whether the number is larger. On PY2025 data
this changed no findings - every corroborated case cleared it, the narrowest at
1.71 standard errors - which is the point: it is what keeps next year's
twelve-appeal fluke out of the report.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.db import query
from app.outliers import exceeds_by_more_than_chance, modified_z_scores

# Ordered by how much evidence stands behind the finding.
CONCERN_ORDER = (
    "denials_overturned_on_appeal",
    "extreme_denials_upheld",
    "extreme_denials_unverifiable",
)


@dataclass(frozen=True)
class Finding:
    issuer_id: str
    issuer_name: str
    state: str
    primary_market: str
    market_count: int
    claims_received: int
    claims_denied: int
    denial_rate: float
    market_median: float
    modified_z: float
    overturn_rate: float | None
    market_overturn_median: float | None
    appeals_filed: int | None
    peer_count: int
    concern: str

    @property
    def times_median(self) -> float:
        return self.denial_rate / self.market_median if self.market_median else 0.0


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def classify(
    modified_z: float,
    overturn_rate: float | None,
    market_overturn_median: float | None,
    outlier_threshold: float,
    watch_threshold: float,
    appeals_filed: int | None = None,
) -> str | None:
    """Decide whether an issuer is worth reporting, and on what evidence.

    Returns None when there is nothing to say.

    * `denials_overturned_on_appeal` - elevated denials AND a share of them
      reversed on appeal that is significantly above the market median. Two
      signals agreeing.
    * `extreme_denials_upheld` - statistically extreme denials whose appeals
      largely stand. Strict, and possibly correct.
    * `extreme_denials_unverifiable` - statistically extreme denials with no
      appeal data reported, so there is no way to tell which of the above it is.
      Said out loud rather than ranked as though we knew.

    Corroboration is a significance test, not a comparison. Appeal volumes in
    this dataset span two orders of magnitude, and being a point above the
    median over 34 appeals is not the same evidence as being a point above it
    over 2,220. `appeals_filed` defaults to None, which fails the test - an
    unknown sample size cannot corroborate anything.
    """
    corroborated = exceeds_by_more_than_chance(
        overturn_rate, market_overturn_median, appeals_filed
    )

    if modified_z > watch_threshold and corroborated:
        return "denials_overturned_on_appeal"
    if modified_z > outlier_threshold:
        if overturn_rate is None:
            return "extreme_denials_unverifiable"
        return "extreme_denials_upheld"
    return None


def find_concerning_issuers(
    connection, min_claims: int | None = None, limit: int | None = None
) -> list[Finding]:
    """Rank issuers whose denial behaviour is abnormal for their own market."""
    threshold = settings.min_claims_for_analysis if min_claims is None else min_claims

    rows = query(
        connection,
        """
        SELECT r.*, b.median_denial_rate
        FROM issuer_denial_rates r
        JOIN market_benchmarks  b USING (primary_market)
        WHERE r.claims_received >= ?
        """,
        (threshold,),
    )

    findings: list[Finding] = []

    for market in sorted({row["primary_market"] for row in rows}):
        peers = [row for row in rows if row["primary_market"] == market]
        if len(peers) < settings.min_peers_for_comparison:
            # A modified z-score over a handful of issuers is not evidence.
            continue

        scores = modified_z_scores([row["denial_rate"] for row in peers])
        overturn_median = median(
            [row["overturn_rate"] for row in peers if row["overturn_rate"] is not None]
        )

        for row, score in zip(peers, scores, strict=True):
            concern = classify(
                float(score),
                row["overturn_rate"],
                overturn_median,
                settings.outlier_threshold,
                settings.watch_threshold,
                row["appeals_filed"],
            )
            if concern is None:
                continue
            findings.append(
                Finding(
                    issuer_id=row["issuer_id"],
                    issuer_name=row["issuer_name"],
                    state=row["state"],
                    primary_market=row["primary_market"],
                    market_count=row["market_count"],
                    claims_received=row["claims_received"],
                    claims_denied=row["claims_denied"],
                    denial_rate=row["denial_rate"],
                    market_median=row["median_denial_rate"],
                    modified_z=float(score),
                    overturn_rate=row["overturn_rate"],
                    market_overturn_median=overturn_median,
                    appeals_filed=row["appeals_filed"],
                    peer_count=len(peers),
                    concern=concern,
                )
            )

    # Within a concern tier, rank by how many members the behaviour affected.
    # A 39% denial rate over 3,000 claims and over 13 million are not the same
    # finding, and sorting purely by z-score would put the small one first.
    findings.sort(
        key=lambda f: (CONCERN_ORDER.index(f.concern), -f.claims_denied)
    )
    return findings[:limit] if limit else findings
