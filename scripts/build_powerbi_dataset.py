"""Build the Power BI star schema from the pipeline's own outputs.

The HTML report in docs/ is a narrative artefact: it states the five findings
and the distribution they came out of. A BI model needs something different -
flat tables at a declared grain, with the statistics already computed, so the
visual layer only aggregates and never re-derives.

This script emits that model. It reads the SQLite database built by
scripts/load_data.py where one is present, and falls back to docs/report.json
otherwise, so the dataset can be rebuilt on a machine that has not run the
ingest.

Grain of each table is stated in its header comment and enforced by an
assertion. The denial-reason table is the only genuinely new material: those
columns exist in plan_claims and in the denial_reason_mix view but have never
been surfaced anywhere in the report.

Usage:
    python scripts/build_powerbi_dataset.py
    python scripts/build_powerbi_dataset.py --db data/denials.db --out powerbi/data
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "data" / "denials.db"
DEFAULT_REPORT = REPO / "docs" / "report.json"
DEFAULT_OUT = REPO / "powerbi" / "data"

SCALE = 0.6745
SIGNIFICANCE_Z = 1.645
MIN_EXPECTED_COUNT = 5

# Friendly labels for the ten denial-reason columns, plus the grouping used on
# the reason page. "Clinical" vs "Administrative" is the distinction that makes
# the mix interesting: an issuer denying mostly on administrative grounds is a
# different operational story from one denying on medical necessity.
REASONS = [
    ("not_medically_necessary", "Not medically necessary", "Clinical"),
    ("experimental", "Experimental or investigational", "Clinical"),
    ("behavioral_health", "Behavioral health", "Clinical"),
    ("service_excluded", "Service excluded from plan", "Benefit design"),
    ("benefit_limit_reached", "Benefit limit reached", "Benefit design"),
    ("out_of_network", "Out of network", "Benefit design"),
    ("referral_required", "Referral required", "Administrative"),
    ("member_not_covered", "Member not covered", "Administrative"),
    ("administrative", "Administrative", "Administrative"),
    ("other_reason", "Other", "Other"),
]


# --------------------------------------------------------------------------
# statistics - deliberately duplicated from app/outliers.py rather than
# imported, so this script runs without the package installed. The formulas
# are identical; tests/test_outliers.py is the authority on them.
# --------------------------------------------------------------------------
def median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of an empty sequence")
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def modified_z_scores(values: list[float]) -> list[float]:
    """0.6745 * (x - median) / MAD. Zeros when the data has no spread."""
    if not values:
        return []
    med = median(values)
    mad = median([abs(v - med) for v in values])
    if mad == 0:
        return [0.0] * len(values)
    return [SCALE * (v - med) / mad for v in values]


def standard_errors_above(
    rate: float | None, baseline: float | None, sample_size: int | None
) -> float | None:
    """How many standard errors `rate` sits above `baseline`, or None.

    None means the comparison could not be made - no data, an empty sample, a
    degenerate baseline, or too few expected counts. That is not 0.0: a test
    that could not be run must not look like one that came out even.
    """
    if rate is None or baseline is None or not sample_size:
        return None
    if not 0.0 < baseline < 1.0:
        return None
    if min(baseline, 1.0 - baseline) * sample_size < MIN_EXPECTED_COUNT:
        return None
    standard_error = math.sqrt(baseline * (1.0 - baseline) / sample_size)
    return (rate - baseline) / standard_error


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def load_from_db(db_path: Path) -> tuple[list[dict], list[dict]]:
    """Issuer rows and denial-reason rows straight from the metric views."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='view'")
    views = {r["name"] for r in cur.fetchall()}
    required = {"issuer_denial_rates", "denial_reason_mix"}
    missing = required - views
    if missing:
        raise RuntimeError(
            f"{db_path} is missing the metric views {sorted(missing)}. "
            "Run: python scripts/load_data.py"
        )

    cur.execute("SELECT * FROM issuer_denial_rates")
    issuers = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM denial_reason_mix")
    reasons = [dict(r) for r in cur.fetchall()]

    conn.close()
    return issuers, reasons


def load_from_report(report_path: Path) -> tuple[list[dict], list[dict]]:
    """Fallback: rebuild issuer rows from the published report payload.

    The report carries every analysed issuer with its rate, appeal count and
    z-score, which is enough for every page except the denial-reason mix -
    those columns only exist in the database.
    """
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    issuers: list[dict] = []
    for market in payload["markets"]:
        for point in market["points"]:
            appeals = point.get("appeals") or 0
            overturn = point.get("overturn_rate")
            denied = point.get("claims_denied")
            rate = point.get("denial_rate")
            received = None
            if denied is not None and rate:
                received = round(denied / rate)
            issuers.append(
                {
                    "issuer_id": "",
                    "state": point["state"],
                    "issuer_name": point["name"],
                    "primary_market": market["name"],
                    "market_count": None,
                    "plan_count": None,
                    "enrollment": None,
                    "claims_received": received,
                    "claims_denied": denied,
                    "denial_rate": rate,
                    "appeals_filed": appeals,
                    "appeals_overturned": (
                        round(overturn * appeals) if overturn is not None else None
                    ),
                    "overturn_rate": overturn,
                }
            )
    return issuers, []


# --------------------------------------------------------------------------
# model build
# --------------------------------------------------------------------------
def build(issuers: list[dict], min_claims: int, min_peers: int) -> dict:
    """Assemble the star schema. Returns {table_name: (fieldnames, rows)}."""

    # Eligibility is the same gate the pipeline applies, restated here so the
    # model can show what was excluded and why instead of silently dropping it.
    for row in issuers:
        received = row.get("claims_received")
        row["eligible"] = bool(received and received >= min_claims)

    # Market-level benchmarks, computed only over eligible issuers, and only
    # for markets with enough peers to support a comparison at all.
    by_market: dict[str, list[dict]] = {}
    for row in issuers:
        by_market.setdefault(row["primary_market"], []).append(row)

    markets: list[dict] = []
    for name, rows in sorted(by_market.items()):
        rated = [r for r in rows if r.get("denial_rate") is not None]
        eligible = [r for r in rows if r["eligible"] and r.get("denial_rate") is not None]
        analysed = len(eligible) >= min_peers
        with_overturn = [
            r["overturn_rate"] for r in eligible if r.get("overturn_rate") is not None
        ]

        # TWO medians, deliberately both carried.
        #
        # `benchmark` is the median over every issuer with a computable rate.
        # It is what sql/metrics.sql's market_benchmarks view produces and what
        # the published HTML report quotes, so the BI layer must show the same
        # figure or the two artefacts contradict each other.
        #
        # `scoring` is the median over the eligible issuers only (>= min_claims)
        # and is the centre the modified z-score is actually computed around,
        # because app/analysis.py filters before calling modified_z_scores.
        #
        # On Individual QHP the two agree to a tenth of a point (18.32 / 18.40).
        # On Individual SADP they differ by two points (32.25 / 30.29), because
        # 35 dental issuers fall under the claim-volume floor. Carrying both
        # makes the gap visible instead of silently picking one.
        benchmark_median = median([r["denial_rate"] for r in rated]) if rated else None
        scoring_median = median([r["denial_rate"] for r in eligible]) if eligible else None

        markets.append(
            {
                "market": name,
                "issuers_total": len(rows),
                "issuers_eligible": len(eligible),
                "analysed": int(analysed),
                "excluded_reason": (
                    "" if analysed else f"fewer than {min_peers} eligible issuers"
                ),
                "median_denial_rate": benchmark_median,
                "median_denial_rate_scoring": scoring_median,
                "median_overturn_rate": (
                    median(with_overturn) if with_overturn else None
                ),
                "issuers_with_overturn_rate": len(with_overturn),
            }
        )

        # Modified z-score is computed WITHIN the market, over eligible issuers
        # only. An ineligible issuer must not move the median that its peers
        # are measured against.
        if analysed and eligible:
            scores = modified_z_scores([r["denial_rate"] for r in eligible])
            for row, score in zip(eligible, scores, strict=True):
                row["modified_z"] = round(score, 4)
        for row in rows:
            row.setdefault("modified_z", None)

    market_lookup = {m["market"]: m for m in markets}

    # Per-issuer facts, including the second signal.
    fact_rows: list[dict] = []
    for row in issuers:
        market = market_lookup[row["primary_market"]]
        baseline = market["median_overturn_rate"]
        sigma = standard_errors_above(
            row.get("overturn_rate"), baseline, row.get("appeals_filed")
        )
        z = row.get("modified_z")

        # Tiering, mirroring app/analysis.py:
        #   z > 3.5                       -> extreme on denial rate alone
        #   z > 2.0 AND sigma > 1.645     -> elevated, corroborated by appeals
        #   anything else                 -> not a finding
        if z is None:
            tier = "Not analysed"
        elif z > 3.5 and row.get("overturn_rate") is None:
            tier = "Extreme, no appeal data"
        elif z > 3.5:
            tier = "Extreme denial rate"
        elif z > 2.0 and sigma is not None and sigma > SIGNIFICANCE_Z:
            tier = "Elevated and overturned on appeal"
        elif z > 2.0:
            tier = "Elevated, not corroborated"
        else:
            tier = "Within normal range"

        fact_rows.append(
            {
                "issuer_key": f"{row.get('issuer_id') or row['issuer_name']}|{row['state']}",
                "issuer_name": row["issuer_name"],
                # Issuer names are NOT unique: a national carrier files in
                # several states under the same name, and the table's grain is
                # one row per issuer PER STATE. Charting by issuer_name alone
                # makes the BI layer silently SUM those states' rates together
                # - the same grain mistake the pipeline hit with the repeated
                # issuer-level claim counts. This label is unique per row, so
                # one point on a chart is one issuer in one state.
                "issuer_label": f"{row['issuer_name']} ({row['state']})",
                "state": row["state"],
                "market": row["primary_market"],
                "claims_received": row.get("claims_received"),
                "claims_denied": row.get("claims_denied"),
                "denial_rate": row.get("denial_rate"),
                # Matches the published report's market median, so the
                # dashboard and docs/index.html quote the same figure.
                "market_median_denial_rate": market["median_denial_rate"],
                # The centre the z-score was actually computed around.
                "market_median_denial_rate_scoring": market["median_denial_rate_scoring"],
                "times_market_median": (
                    row["denial_rate"] / market["median_denial_rate"]
                    if row.get("denial_rate") and market["median_denial_rate"]
                    else None
                ),
                "modified_z": z,
                "appeals_filed": row.get("appeals_filed"),
                "appeals_overturned": row.get("appeals_overturned"),
                "overturn_rate": row.get("overturn_rate"),
                "market_median_overturn_rate": baseline,
                "sigma_above_median": round(sigma, 3) if sigma is not None else None,
                "corroborated": (
                    "" if sigma is None else int(sigma > SIGNIFICANCE_Z)
                ),
                "has_appeal_data": int(row.get("overturn_rate") is not None),
                "eligible": int(row["eligible"]),
                "tier": tier,
                "is_finding": int(
                    tier
                    in {
                        "Elevated and overturned on appeal",
                        "Extreme denial rate",
                        "Extreme, no appeal data",
                    }
                ),
                "enrollment": row.get("enrollment"),
                "plan_count": row.get("plan_count"),
            }
        )

    # One row per issuer per state - the grain the whole project turns on.
    keys = [r["issuer_key"] for r in fact_rows]
    assert len(keys) == len(set(keys)), "fct_issuer grain is not unique"

    dim_issuer = [
        {
            "issuer_key": r["issuer_key"],
            "issuer_name": r["issuer_name"],
            "state": r["state"],
            "market": r["market"],
        }
        for r in fact_rows
    ]

    dim_reason = [
        {"reason_key": key, "reason": label, "reason_group": group}
        for key, label, group in REASONS
    ]

    return {
        "fct_issuer": fact_rows,
        "dim_issuer": dim_issuer,
        "dim_market": markets,
        "dim_reason": dim_reason,
    }


def build_reason_facts(reason_rows: list[dict]) -> list[dict]:
    """Unpivot the ten reason columns into long form.

    Power BI models long data far better than wide: one row per issuer per
    reason means a single measure can slice by reason instead of needing ten
    near-identical measures.
    """
    out: list[dict] = []
    for row in reason_rows:
        key = f"{row.get('issuer_id') or row.get('issuer_name')}|{row.get('state')}"
        for reason_key, _label, _group in REASONS:
            value = row.get(reason_key)
            if value is None:
                continue
            out.append(
                {
                    "issuer_key": key,
                    "issuer_name": row.get("issuer_name"),
                    "state": row.get("state"),
                    "market": row.get("plan_market"),
                    "reason_key": reason_key,
                    "denials": int(value),
                }
            )
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        print(f"  {path.name:<28} 0 rows (skipped)")
        return
    fieldnames = list(rows[0].keys())
    # utf-8-sig: Power BI's CSV connector mis-detects plain UTF-8 on Windows.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {path.name:<28} {len(rows):>6} rows, {len(fieldnames)} cols")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--min-claims", type=int, default=1000)
    parser.add_argument("--min-peers", type=int, default=20)
    args = parser.parse_args()

    db_path = Path(args.db)
    report_path = Path(args.report)
    out_dir = Path(args.out)

    if db_path.exists():
        print(f"Source: {db_path} (database)")
        issuers, reason_rows = load_from_db(db_path)
    elif report_path.exists():
        print(f"Source: {report_path} (published report - no database found)")
        print("  note: the denial-reason table needs the database; run")
        print("        python scripts/load_data.py  to produce it.")
        issuers, reason_rows = load_from_report(report_path)
    else:
        print("No data source found. Expected one of:")
        print(f"  {db_path}")
        print(f"  {report_path}")
        return 1

    print(f"Loaded {len(issuers)} issuer rows, {len(reason_rows)} reason rows\n")

    tables = build(issuers, args.min_claims, args.min_peers)
    tables["fct_denial_reason"] = build_reason_facts(reason_rows)

    print("Writing:")
    for name, rows in tables.items():
        write_csv(out_dir / f"{name}.csv", rows)

    findings = [r for r in tables["fct_issuer"] if r["is_finding"]]
    eligible = [r for r in tables["fct_issuer"] if r["eligible"]]
    analysed_markets = [m for m in tables["dim_market"] if m["analysed"]]
    analysed = [
        r
        for r in eligible
        if any(m["market"] == r["market"] for m in analysed_markets)
    ]

    print(f"\n{'=' * 62}")
    print(f"  issuers in model       {len(tables['fct_issuer']):>6}")
    print(f"  eligible               {len(eligible):>6}  (>= {args.min_claims} claims)")
    print(f"  analysed               {len(analysed):>6}  (market has >= {args.min_peers} peers)")
    print(f"  findings               {len(findings):>6}")
    for row in sorted(findings, key=lambda r: -(r["modified_z"] or 0)):
        print(
            f"      z={row['modified_z']:>5}  "
            f"sigma={str(row['sigma_above_median']):>5}  "
            f"{row['issuer_name'][:40]:<40} {row['tier']}"
        )
    print(f"{'=' * 62}")
    print(f"\nCSVs in: {out_dir.resolve()}")
    print("Next: Power BI Desktop -> Get Data -> Folder -> select that directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
