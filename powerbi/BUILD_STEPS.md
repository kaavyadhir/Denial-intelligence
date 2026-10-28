# Building the Power BI report — click by click

Target: **45 minutes.** Steps 1–6 are the core and must get done. Step 7 is the
extra page; skip it if time runs short, the report stands without it.

---

## Step 0 — before you start

Power BI Desktop, free, Windows only. If it isn't installed, start that
download **now** and do steps 1–2 while it installs.

> Microsoft Store → search "Power BI Desktop" → Install

---

## Step 1 — produce the data (2 min)

In PowerShell, from the project folder:

```powershell
cd D:\MY_PROJECTS\denial-intelligence
python scripts\build_powerbi_dataset.py
```

You should see five CSVs written into `powerbi\data` and this at the bottom:

```
  issuers in model          289
  eligible                  289
  analysed                  284
  findings                    5
```

If `fct_denial_reason.csv` says **0 rows (skipped)**, the database isn't built.
The denial-reason page (step 7) needs it. To get it:

```powershell
python scripts\load_data.py
python scripts\build_powerbi_dataset.py
```

If `load_data.py` needs the raw CMS workbook and you no longer have it, skip
step 7 — everything else works from `docs\report.json`.

---

## Step 2 — load into Power BI (3 min)

1. Open Power BI Desktop → **Get data** → **Text/CSV**
2. Load each of these from `D:\MY_PROJECTS\denial-intelligence\powerbi\data`:
   - `fct_issuer.csv`
   - `dim_market.csv`
   - `dim_issuer.csv`
   - `dim_reason.csv`
   - `fct_denial_reason.csv` *(only if it has rows)*
3. For each one click **Transform Data** first, check the column types look
   right (rates should be **Decimal Number**, not Text), then **Close & Apply**.

> If a rate column comes in as Text, it's the decimal separator. In Power Query:
> right-click the column → **Change Type** → **Using Locale** → Decimal Number,
> English (United States).

---

## Step 3 — build the star schema (5 min)

**Model view** (the third icon down the left side). Drag these joins:

| From | To | Cardinality |
|---|---|---|
| `dim_issuer[issuer_key]` | `fct_issuer[issuer_key]` | 1 → * |
| `dim_issuer[issuer_key]` | `fct_denial_reason[issuer_key]` | 1 → * |
| `dim_market[market]` | `dim_issuer[market]` | 1 → * |
| `dim_reason[reason_key]` | `fct_denial_reason[reason_key]` | 1 → * |

Delete any relationship Power BI auto-created that isn't in that list.

**Why this matters, and they may ask:** `dim_issuer` is a conformed dimension —
it sits between the two fact tables so a single issuer slicer filters both of
them at once. Facts never join directly to facts.

---

## Step 4 — add the measures (10 min)

Open `powerbi\measures.dax`. For each measure: **Home → New measure**, paste the
formula (not the `//` comment lines), press Enter.

**Add these nine first** — they cover every visual in steps 5–6:

1. `Issuers Total`
2. `Issuers Analysed`
3. `Findings`
4. `Denial Rate`
5. `Median Denial Rate`
6. `Overturn Rate`
7. `Issuers Above Watch`
8. `Elevated Not Reported`
9. `% Without Appeal Data`

Then formatting: select each rate measure → **Measure tools** → format
**Percentage**, 1 decimal place.

Add the rest if you have time. **Do not add a measure you can't explain.**

---

## Step 5 — page 1: "Where the findings came from" (12 min)

Four visuals. Set page background to white, and give every visual a title that
is a *sentence*, not a noun.

**a) Four cards across the top**
- `Issuers Total` → title "Insurers in the CMS file" *(326)*
- `Issuers Eligible` → "Analysed" *(289)*
- `Findings` → "Findings" *(5)*
- `% Without Appeal Data` → "No appeal data filed" *(51%)*

> Use **`Issuers Eligible`**, not `Issuers Analysed`, on that second card. Eligible
> is 289 and matches the headline in your HTML report; `Issuers Analysed` is 284
> because the 5 SHOP issuers are excluded from scoring. Both are correct, but the
> dashboard and the report must not show different numbers for the same word.

**b) Scatter chart — the whole thesis in one visual**
- X axis: `fct_issuer[denial_rate]`
- Y axis: `fct_issuer[overturn_rate]`
- Size: `fct_issuer[appeals_filed]`
- Legend: `fct_issuer[tier]`
- Details: `dim_issuer[issuer_name]`
- Title: **"Denying more than peers, and losing the appeals"**
- Analytics pane → add an **X-axis constant line** at `0.183` labelled
  "Individual QHP median" and a **Y-axis constant line** at `0.425`.

The four quadrants are the argument: top-right is where the findings live.

**c) Column chart — the market point**
- X axis: `dim_market[market]`
- Y axis: `Median Denial Rate`
- Title: **"Dental denies more than medical as a matter of course"**

This shows 32.3% vs 18.3% and is the visual answer to "isn't this just a
sorted query?"

**d) Slicer**
- Field: `dim_market[market]`, style Tile

---

## Step 6 — page 2: "The five findings" (10 min)

**a) Table visual**

Columns, in this order:

| Field | Format |
|---|---|
| `dim_issuer[issuer_name]` | |
| `dim_issuer[state]` | |
| `fct_issuer[denial_rate]` | Percentage, 1 dp |
| `fct_issuer[times_market_median]` | 2 dp, header "× market median" |
| `fct_issuer[modified_z]` | 2 dp, header "Modified z" |
| `fct_issuer[overturn_rate]` | Percentage, 1 dp |
| `fct_issuer[appeals_filed]` | Whole number |
| `fct_issuer[sigma_above_median]` | 2 dp, header "σ above median" |
| `fct_issuer[tier]` | |

Filter the visual: `fct_issuer[is_finding]` **is 1**.

Conditional formatting on `tier`: **Format → Cell elements → Background colour
→ Format by: Field value → `Tier Colour`**.

**b) Two cards beside it**
- `Issuers Above Watch` → title "Cleared the watch threshold (z > 2.0)"
- `Elevated Not Reported` → title "Elevated but NOT reported — no significant appeal evidence"

> **That second card is the most important number on the page.** It says 17.
> Seventeen insurers looked elevated and you did not report them, because the
> appeal evidence didn't clear significance. Point at it in the interview.

**c) A text box underneath**, exactly this:

> Three findings sit between z 2.0 and 3.5 — elevated, not extreme — and are
> reported only because their appeal records clear a one-sided significance
> test. The two extreme cases (z 4.75 and 4.21) filed no appeal data at all,
> so they are labelled unverifiable rather than ranked.

---

## Step 7 — page 3: denial reason mix *(skip if short on time)*

Needs `fct_denial_reason.csv` to have rows.

- **Stacked bar**: Y = `dim_reason[reason]`, X = `Denials by Reason`,
  Legend = `dim_reason[reason_group]`
- **Matrix**: Rows = `dim_issuer[issuer_name]`, Columns = `dim_reason[reason_group]`,
  Values = `% of Reason Mix`
- Slicer: `fct_issuer[tier]`

Title the page **"Why claims were denied"**. This data has never appeared in the
HTML report — it's new analysis, and it's the page to open if someone asks what
you'd build next.

---

## Step 8 — save and capture (3 min)

1. **File → Save as** → `D:\MY_PROJECTS\denial-intelligence\powerbi\DenialIntelligence.pbix`
2. Screenshot both pages → save into `powerbi\screenshots\`
3. Add the screenshots to the repo README so the GitHub page shows them

**.pbix files are large binaries.** Before committing, check the size:

```powershell
Get-ChildItem powerbi\DenialIntelligence.pbix | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,2)}}
```

Under ~25 MB, commit it. Over that, commit the screenshots and the CSVs only
and leave the .pbix out — the build script regenerates everything anyway.

---

## The two medians — know this before anyone asks

`dim_market` carries two median denial rates per market, and they are not the same:

| Market | `median_denial_rate` | `median_denial_rate_scoring` |
|---|---|---|
| Individual QHP | 18.32% | 18.40% |
| Individual SADP | **32.25%** | **30.29%** |
| SHOP | 17.87% | 17.87% |

The first is the median over **every** issuer with a computable rate — it's what
`sql/metrics.sql` produces and what your published report quotes. The second is
the median over only the **eligible** issuers (≥1000 claims), and it's the centre
the modified z-score is actually computed around, because `app/analysis.py`
filters before scoring.

On dental they differ by two points, because 35 dental issuers sit under the
claim-volume floor.

**Always display the first one** so the dashboard agrees with the report. If
someone notices the gap, the answer is good:

> "They're two different peer sets. The published median is over every issuer
> with a computable rate; the z-score is computed over just the issuers above the
> claim-volume floor, so its centre is slightly different. I found that while
> building the BI layer. It's defensible either way but it should be one choice,
> not two — I'd make the benchmark use the same filtered set the score uses."

---

## The one thing to be straight about

If anyone asks when you built this: **"The pipeline and the HTML report are
from September. I added the Power BI layer on top of it this week."**

That is a completely normal thing to have done and it costs you nothing. Being
caught implying otherwise costs you the interview.
