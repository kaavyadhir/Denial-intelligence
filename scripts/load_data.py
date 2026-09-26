"""Build the database from the CMS Transparency in Coverage PUF and report findings.

Run this after placing the workbook in data/raw/. Everything it prints is
computed by SQL and the rules in app/analysis.py - no language model is
involved at any point in this script.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.analysis import CONCERN_ORDER, find_concerning_issuers  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, initialise, query  # noqa: E402
from app.ingest import load  # noqa: E402

WORKBOOK = "transparency_in_coverage_PUF.xlsx"
SOURCE_URL = ("https://data.healthcare.gov/datafile/py2025/"
              "transparency_in_coverage_PUF.xlsx")

# What each concern tier means, in the words a reader needs rather than the
# identifier the code sorts on.
TIER_HEADINGS = {
    "denials_overturned_on_appeal":
        "Denied more than their peers - and lost the appeals",
    "extreme_denials_upheld":
        "Extreme denial rates, but the appeals largely stood",
    "extreme_denials_unverifiable":
        "Extreme denial rates, no appeal outcomes reported",
}

TIER_NOTES = {
    "denials_overturned_on_appeal":
        "Two independent signals agreeing. The appeal record is the closest\n"
        "  thing this dataset has to a verdict on whether a denial was correct.",
    "extreme_denials_upheld":
        "Strict, and the appeal record does not contradict them.",
    "extreme_denials_unverifiable":
        "No appeals data was filed, so there is no way to tell whether these\n"
        "  denials were justified. Stated rather than assumed either way.",
}


def percent(value: float | None, width: int = 5) -> str:
    return f"{value:.0%}".rjust(width) if value is not None else "n/a".rjust(width)


def report_load(report) -> None:
    for market, count in sorted(report.per_market.items()):
        print(f"  {count:6d} rows   {market}")
    print(f"\n  {report.rows_loaded} rows loaded")
    print(f"  {report.blank_rows} blank rows skipped, "
          f"{report.malformed_rows} unusable rows skipped")

    if report.unmapped_headers:
        print("\n  Columns present in the source but not loaded:")
        for header in report.unmapped_headers:
            print(f"    - {header}")


def report_coverage(connection) -> None:
    issuers = query(connection, "SELECT COUNT(*) AS n FROM issuer_denial_rates")[0]["n"]
    markets = query(
        connection, "SELECT COUNT(*) AS n FROM market_benchmarks")[0]["n"]
    eligible = query(
        connection,
        "SELECT COUNT(*) AS n FROM issuer_denial_rates WHERE claims_received >= ?",
        (settings.min_claims_for_analysis,),
    )[0]["n"]
    print(f"\n  {issuers} issuers have a computable denial rate, "
          f"across {markets} markets")
    print(f"  {eligible} of them cleared the "
          f"{settings.min_claims_for_analysis:,}-claim minimum for analysis")


def report_findings(connection) -> None:
    findings = find_concerning_issuers(connection)

    print(f"\n{'=' * 78}")
    print(f"  {len(findings)} findings")
    print("  Compared within market, against peers in the same market only.")
    print(f"  Elevated: modified z > {settings.watch_threshold} with corroborating "
          f"appeal evidence.")
    print(f"  Extreme:  modified z > {settings.outlier_threshold} on denial rate "
          f"alone.")
    print("=" * 78)

    if not findings:
        print("\n  Nothing met the evidence bar. That is a valid result.")
        return

    for tier in CONCERN_ORDER:
        tiered = [f for f in findings if f.concern == tier]
        if not tiered:
            continue

        print(f"\n  {TIER_HEADINGS[tier]}")
        print(f"  {TIER_NOTES[tier]}")
        print(f"  {'-' * 74}")

        for finding in tiered:
            print(f"    {finding.issuer_name[:52]}")
            print(f"      {finding.state}  {finding.primary_market}"
                  f"   (vs {finding.peer_count} peers)")
            print(f"      denied {finding.denial_rate:.1%}"
                  f"  =  {finding.times_median:.1f}x the market median of "
                  f"{finding.market_median:.1%}"
                  f"   [modified z = {finding.modified_z:.2f}]")
            print(f"      {finding.claims_denied:,} of "
                  f"{finding.claims_received:,} claims denied")
            if finding.overturn_rate is None:
                print("      appeal outcomes: not reported")
            else:
                filed = (f"{finding.appeals_filed:,} appeals filed"
                         if finding.appeals_filed else "appeals filed")
                print(f"      {filed}; {percent(finding.overturn_rate)} overturned "
                      f"(market median {percent(finding.market_overturn_median)})")
            print()


def main() -> int:
    path = os.path.join(settings.raw_data_dir, WORKBOOK)
    if not os.path.exists(path):
        print(f"Not found: {path}", file=sys.stderr)
        print(f"Download it from:\n  {SOURCE_URL}", file=sys.stderr)
        return 1

    connection = connect()
    initialise(connection)

    print(f"Loading {path} ...")
    report_load(load(path, connection))
    report_coverage(connection)
    report_findings(connection)

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
