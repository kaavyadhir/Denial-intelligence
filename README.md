# Denial Intelligence

Finds health insurers whose claim-denial behaviour is abnormal for their own
market, and separates the cases where there is evidence the denials were wrong
from the cases where there is merely a large number.

Built on the CMS Transparency in Coverage public use file (PY2025) — 5,424 plan
rows covering 326 issuers across 3 markets.

Every number in the output is computed by SQL and a set of explicit rules. No
language model is involved in deciding anything.

---

## The finding

```
  Denied more than their peers - and lost the appeals
  --------------------------------------------------------------------------
    Blue Cross and Blue Shield of Alabama
      AL  Individual QHP   (vs 170 peers)
      denied 34.8%  =  1.9x the market median of 18.3%   [modified z = 2.19]
      4,533,017 of 13,033,751 claims denied
      1,730 appeals filed;   47% overturned (market median   43%)

    UnitedHealthcare of Ohio, Inc.
      OH  Individual QHP   (vs 170 peers)
      denied 36.4%  =  2.0x the market median of 18.3%   [modified z = 2.41]
      125,776 of 345,239 claims denied
      342 appeals filed;   47% overturned (market median   43%)

    AmeriHealth Caritas North Carolina, Inc.
      NC  Individual QHP   (vs 170 peers)
      denied 39.1%  =  2.1x the market median of 18.3%   [modified z = 2.78]
      81,208 of 207,490 claims denied
      48 appeals filed;   71% overturned (market median   43%)

  Extreme denial rates, no appeal outcomes reported
  --------------------------------------------------------------------------
    AmeriHealth Caritas Florida, Inc.     FL   denied 53.9%   z = 4.75
    AmeriHealth Caritas VIP Next, Inc.    DE   denied 49.9%   z = 4.21
```

Five findings out of 289 eligible issuers. The interesting part is not that
three insurers denied a lot of claims — it is that when their members pushed
back, the insurer reversed itself more often than its peers did. The appeal
outcome is the closest thing this dataset has to a verdict on whether a denial
should have happened.

---

## Why the obvious version of this is wrong

Four decisions separate this from `ORDER BY denial_rate DESC`. Each one changed
the output.

### 1. Compare inside a market, not across all of them

The first version ranked every issuer together and returned a list that was
entirely dental plans. Dental is not scandalous; it is just different:

| market | issuers | median denial rate |
|---|---|---|
| Individual QHP (medical) | 172 | 18.3% |
| Individual SADP (dental) | 149 | 32.3% |
| SHOP | 5 | 17.9% |

A dental plan denying 32% of claims is exactly average. Ranking across markets
finds the *market*, not the outlier. Every comparison here is against peers in
the same market.

SHOP has five issuers, so it is excluded entirely — a median over five values
is not a benchmark (`min_peers_for_comparison = 20`).

### 2. The claim counts are issuer-wide, not per-market

`Issuer_Claims_Received` is reported per issuer, not per plan or per market. It
repeats identically on every row that issuer appears on. Blue Cross Alabama's
13,033,751 claims appear in all three market sheets.

Grouping by `(market, issuer)` — the natural thing to write — entered that one
insurer three times, once per market, and compared its entire book against the
small set of issuers in each. The fix was to collapse each issuer to
`(issuer_id, state)` and attribute it to the market where it sells the most
plans:

```sql
CREATE VIEW issuer_primary_market AS
WITH counts AS (
    SELECT issuer_id, state, plan_market, COUNT(*) AS plans,
           ROW_NUMBER() OVER (
               PARTITION BY issuer_id, state
               ORDER BY COUNT(*) DESC, plan_market
           ) AS rank
    FROM plan_claims
    GROUP BY issuer_id, state, plan_market
)
SELECT issuer_id, state, plan_market AS primary_market, plans
FROM counts WHERE rank = 1;
```

`(issuer_id, state)` rather than `issuer_id` alone because HIOS issuer IDs are
assigned per state — that pair is the real identity.

### 3. Median and MAD, not mean and standard deviation

Outliers inflate the standard deviation, which raises the very threshold meant
to catch them. Measured on synthetic denial-rate distributions
(`tests/test_outliers.py`):

| planted outliers | modified z (MAD) | ordinary z |
|---|---|---|
| 2 extreme (55%, 61%) | 2 of 2 | 2 of 2 |
| 6 elevated (45–58%) | **6 of 6** | **0 of 6** |
| 3 mild (22–25%) | **3 of 3** | **0 of 3** |

With one or two extreme values both methods work. The divergence shows up where
this data actually lives — several insurers elevated together. Six issuers
around 50% raise the standard deviation enough that the ordinary method flags
none of them. The median and MAD are unmoved by extreme values.

### 4. Two signals beat one, but the second signal has to be real

A single modified-z cut at 3.5 surfaced two small issuers with no appeal data at
all — and discarded every case where the appeal record actually showed denials
being overturned, including one insurer reversing 71% of them.

So there are two thresholds. `z > 3.5` on denial rate alone is reportable.
`z > 2.0` is reportable only when the appeal record corroborates it.

Corroboration is a one-sided significance test, not `overturn_rate > median`.
Appeal volumes here run from 34 to 2,220, and a four-point gap means something
very different at each end:

| insurer | overturn | appeals | gap vs 43% median |
|---|---|---|---|
| Blue Cross Alabama | 46.6% | 1,730 | 3.4 σ — evidence |
| UnitedHealthcare Ohio | 47.1% | 342 | 1.7 σ — just clears it |
| *same rate, 40 appeals* | 47.0% | 40 | 0.5 σ — noise |

On PY2025 data the test changed no findings; the narrowest case cleared it at
1.71 standard errors. That is the point — it is what keeps next year's
twelve-appeal fluke out of the report.

---

## Data handling

**Missing is not zero.** The source uses `""`, `NA`, `N/A`, `*`, `--` and
several other spellings for "not reported". Coercing those to `0` would turn an
insurer that filed no appeals report into one with a 0% overturn rate, which
reads as a *good* result. They become `NULL`, and 166 of 326 issuers — just over
half — end up with no computable overturn rate at all. Those issuers can still
be flagged as extreme, but they are labelled `extreme_denials_unverifiable`
rather than ranked as though their denials had been reviewed.

**Columns are matched by name, not position.** Header text is normalised
(lowercased, non-alphanumerics stripped) before matching. The file ships with
`Enrolle` and `Proceduce` misspelled in column headers; the loader accepts both
those and the correct spellings, so it survives CMS fixing them.

**Unmapped columns are reported, not silently dropped.** The loader prints every
source column it did not load, so a schema change is visible on the next run
instead of quietly shrinking the analysis.

**Blank rows and malformed rows are counted separately.** 2,300 blank rows is a
spreadsheet artefact and expected. 2,300 *malformed* rows would mean the parser
is broken. Collapsing them into one "skipped" number hides the difference.

---

## Running it

```bash
pip install -r requirements-dev.txt

# Download the source workbook into data/raw/
#   https://data.healthcare.gov/datafile/py2025/transparency_in_coverage_PUF.xlsx

python scripts/load_data.py
```

Builds a SQLite database and prints the load report, the coverage summary and
the tiered findings.

```bash
pytest -q        # 83 tests
```

---

## Layout

```
app/coerce.py      "NA", "*", "" -> None. Never 0.
app/ingest.py      Workbook -> rows. Name-based column matching.
app/db.py          Connection, schema, query helper.
app/outliers.py    Modified z-score (MAD); one-sided proportion test.
app/analysis.py    The rules: which issuers are reportable, and on what evidence.
sql/schema.sql     plan_claims table.
sql/metrics.sql    Every rate. Issuer dedup, market and state benchmarks.
scripts/load_data.py
```

The split is deliberate: SQL computes every *rate*, `app/analysis.py` decides
which rates are unusual. Neither step involves a language model.

---

## What this does not show

- **One year of data.** A single year cannot distinguish a policy change from a
  reporting change. Year-over-year comparison is the obvious next step.
- **In-network claims only.** Out-of-network denials are reported separately and
  are not loaded.
- **Self-reported.** Issuers report their own denial and appeal counts to CMS.
  An issuer that under-reports denials looks better here.
- **A high denial rate is not proof of wrongdoing.** It can reflect a sicker
  population, a stricter benefit design, or accurate adjudication of bad claims.
  That is precisely why the appeal outcome is required before the strongest
  label is applied — and why the other two tiers are named for what is actually
  known rather than what is implied.

---

## Source

Centers for Medicare & Medicaid Services, *Transparency in Coverage PUF*, plan
year 2025. Public domain.
