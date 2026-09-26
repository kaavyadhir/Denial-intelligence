"""Render the analysis as a standalone HTML report.

Reads the database, runs the same rules the CLI does, and writes a single
self-contained page to docs/. Nothing is fetched at view time - the page is a
snapshot of one run, which is what makes it publishable as a static file and
honest about being one year of data rather than a live feed.

    python scripts/build_report.py

Writes docs/index.html and docs/report.json. The JSON is the same data the page
draws from, kept separate so the numbers can be checked without reading HTML.
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.analysis import CONCERN_ORDER, find_concerning_issuers, median  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, query  # noqa: E402
from app.outliers import (  # noqa: E402
    MIN_EXPECTED_COUNT,
    SIGNIFICANCE_Z,
    modified_z_scores,
    standard_errors_above,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "scripts", "report_template.html")
OUT_DIR = os.path.join(ROOT, "docs")

# The funnel chart needs one market to be readable. Individual QHP is the
# largest and the only one with findings; the page says so rather than
# implying the others were examined the same way.
FUNNEL_MARKET = "Individual QHP"

TIER_LABEL = {
    "denials_overturned_on_appeal": "Denied more than peers, and lost the appeals",
    "extreme_denials_upheld": "Extreme denial rate, appeals largely stood",
    "extreme_denials_unverifiable": "Extreme denial rate, no appeal outcomes reported",
}

TIER_NOTE = {
    "denials_overturned_on_appeal":
        "Two independent signals agreeing. The appeal outcome is the closest "
        "thing this dataset has to a verdict on whether a denial was correct.",
    "extreme_denials_upheld":
        "Strict, and the appeal record does not contradict them.",
    "extreme_denials_unverifiable":
        "No appeal outcomes were filed, so there is no way to tell whether "
        "these denials were justified. Stated rather than assumed either way.",
}

# Charts use two reserved status colours plus muted grey, and every flagged
# mark also carries a shape and a direct label - hue never carries meaning
# alone. See the note in report_template.html.
TIER_TONE = {
    "denials_overturned_on_appeal": "critical",
    "extreme_denials_upheld": "warning",
    "extreme_denials_unverifiable": "warning",
}


def market_rows(connection) -> list[dict]:
    """Every issuer eligible for analysis, with its score and market median."""
    rows = query(
        connection,
        """
        SELECT r.*, b.median_denial_rate
        FROM issuer_denial_rates r
        JOIN market_benchmarks  b USING (primary_market)
        WHERE r.claims_received >= ?
        """,
        (settings.min_claims_for_analysis,),
    )

    enriched: list[dict] = []
    for market in sorted({row["primary_market"] for row in rows}):
        peers = [row for row in rows if row["primary_market"] == market]
        scores = modified_z_scores([row["denial_rate"] for row in peers])
        for row, score in zip(peers, scores, strict=True):
            enriched.append({**dict(row), "modified_z": float(score)})
    return enriched


def build_payload(connection) -> dict:
    rows = market_rows(connection)
    findings = find_concerning_issuers(connection)
    flagged = {(f.issuer_id, f.state): f.concern for f in findings}

    markets = []
    for market in sorted({row["primary_market"] for row in rows}):
        peers = [row for row in rows if row["primary_market"] == market]
        overturns = [r["overturn_rate"] for r in peers if r["overturn_rate"] is not None]
        markets.append({
            "name": market,
            "issuers": len(peers),
            "analysed": len(peers) >= settings.min_peers_for_comparison,
            "median_denial_rate": peers[0]["median_denial_rate"],
            "median_overturn_rate": median(overturns),
            "points": [
                {
                    "name": r["issuer_name"],
                    "state": r["state"],
                    "denial_rate": r["denial_rate"],
                    "overturn_rate": r["overturn_rate"],
                    "appeals": r["appeals_filed"],
                    "claims_denied": r["claims_denied"],
                    "z": round(r["modified_z"], 2),
                    "concern": flagged.get((r["issuer_id"], r["state"])),
                }
                for r in peers
            ],
        })

    total_issuers = query(
        connection, "SELECT COUNT(*) AS n FROM issuer_denial_rates")[0]["n"]
    no_appeal_data = query(
        connection,
        "SELECT COUNT(*) AS n FROM issuer_denial_rates WHERE overturn_rate IS NULL",
    )[0]["n"]

    return {
        "generated": date.today().isoformat(),
        "source": "CMS Transparency in Coverage PUF, plan year 2025",
        "totals": {
            "issuers": total_issuers,
            "analysed": len(rows),
            "findings": len(findings),
            "no_appeal_data": no_appeal_data,
            "no_appeal_share": no_appeal_data / total_issuers if total_issuers else 0,
        },
        "thresholds": {
            "outlier": settings.outlier_threshold,
            "watch": settings.watch_threshold,
            "min_claims": settings.min_claims_for_analysis,
            "min_peers": settings.min_peers_for_comparison,
            "significance_z": SIGNIFICANCE_Z,
            "min_expected": MIN_EXPECTED_COUNT,
        },
        "markets": markets,
        "funnel_market": FUNNEL_MARKET,
        "tier_order": list(CONCERN_ORDER),
        "tier_label": TIER_LABEL,
        "tier_note": TIER_NOTE,
        "tier_tone": TIER_TONE,
        "findings": [
            {
                "name": f.issuer_name,
                "state": f.state,
                "market": f.primary_market,
                "peers": f.peer_count,
                "denial_rate": f.denial_rate,
                "market_median": f.market_median,
                "times_median": f.times_median,
                "z": round(f.modified_z, 2),
                "claims_received": f.claims_received,
                "claims_denied": f.claims_denied,
                "overturn_rate": f.overturn_rate,
                "market_overturn_median": f.market_overturn_median,
                "appeals": f.appeals_filed,
                "sigma": sigma_above(
                    f.overturn_rate, f.market_overturn_median, f.appeals_filed
                ),
                "concern": f.concern,
            }
            for f in findings
        ],
    }


def sigma_above(
    rate: float | None, baseline: float | None, sample: int | None
) -> float | None:
    """The corroboration margin, rounded for display.

    Shown so a reader can see *how far* past the line each case was, rather
    than only that it passed. The narrowest case here clears by 1.71. It calls
    the same function the rule does, so the page cannot disagree with the
    decision it is reporting.
    """
    score = standard_errors_above(rate, baseline, sample)
    return None if score is None else round(score, 2)


def main() -> int:
    if not os.path.exists(settings.database_path):
        print(f"No database at {settings.database_path}.", file=sys.stderr)
        print("Run scripts/load_data.py first.", file=sys.stderr)
        return 1

    connection = connect()
    payload = build_payload(connection)
    connection.close()

    os.makedirs(OUT_DIR, exist_ok=True)

    json_path = os.path.join(OUT_DIR, "report.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    with open(TEMPLATE, encoding="utf-8") as handle:
        template = handle.read()

    # json.dumps output is inlined into a <script> block, so the one sequence
    # that could end that block early has to be neutralised.
    embedded = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html_path = os.path.join(OUT_DIR, "index.html")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(template.replace("__REPORT_DATA__", embedded))

    print(f"Wrote {html_path}")
    print(f"Wrote {json_path}")
    print(f"  {payload['totals']['findings']} findings, "
          f"{payload['totals']['analysed']} issuers analysed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
