-- The metric layer. Every number the narrative report states is computed here,
-- in SQL, and handed to the model as a finished value. The model is never asked
-- to calculate anything, so it cannot invent a statistic.

DROP VIEW IF EXISTS denial_reason_mix;
DROP VIEW IF EXISTS state_market_benchmarks;
DROP VIEW IF EXISTS market_benchmarks;
DROP VIEW IF EXISTS issuer_denial_rates;
DROP VIEW IF EXISTS issuer_totals;
DROP VIEW IF EXISTS issuer_primary_market;

-- An issuer that sells in several markets appears on rows in each of them, and
-- its Issuer_Claims_* figures are IDENTICAL in every one - they cover the whole
-- company, not that market's business. Blue Cross and Blue Shield of Alabama
-- carries the same 13,033,751 claims into Individual QHP, Individual SADP and
-- SHOP alike.
--
-- Grouping by (market, issuer) therefore counted such issuers once per market
-- and compared an insurer's entire book against one market's peers. The unit of
-- analysis is the issuer, so these views group by issuer and record which market
-- the issuer mostly operates in as context, not as a filter.
CREATE VIEW issuer_primary_market AS
WITH counts AS (
    SELECT
        issuer_id,
        state,
        plan_market,
        COUNT(*) AS plans,
        ROW_NUMBER() OVER (
            PARTITION BY issuer_id, state
            ORDER BY COUNT(*) DESC, plan_market
        ) AS rank
    FROM plan_claims
    GROUP BY issuer_id, state, plan_market
)
SELECT issuer_id, state, plan_market AS primary_market, plans
FROM counts
WHERE rank = 1;

-- One row per issuer per state. HIOS issuer IDs are assigned per state, so that
-- pair is the real identity. MAX collapses the repeated issuer-level figures;
-- SUM would multiply them by the issuer's plan count.
CREATE VIEW issuer_totals AS
SELECT
    p.issuer_id,
    p.state,
    MAX(p.issuer_name)                          AS issuer_name,
    m.primary_market,
    COUNT(DISTINCT p.plan_market)               AS market_count,
    MAX(p.issuer_claims_received_in_network)    AS claims_received,
    MAX(p.issuer_claims_denied_in_network)      AS claims_denied,
    MAX(p.internal_appeals_filed)               AS appeals_filed,
    MAX(p.internal_appeals_overturned)          AS appeals_overturned,
    SUM(p.average_monthly_enrollment)           AS enrollment,
    COUNT(*)                                    AS plan_count
FROM plan_claims p
JOIN issuer_primary_market m
  ON m.issuer_id = p.issuer_id AND m.state = p.state
GROUP BY p.issuer_id, p.state, m.primary_market;

-- Rates, with rows that cannot support a rate excluded rather than defaulted.
-- An issuer that did not report its denominator has no denial rate; it does not
-- have a denial rate of zero.
CREATE VIEW issuer_denial_rates AS
SELECT
    issuer_id,
    state,
    issuer_name,
    primary_market,
    market_count,
    claims_received,
    claims_denied,
    enrollment,
    plan_count,
    CAST(claims_denied AS REAL) / claims_received AS denial_rate,
    appeals_filed,
    appeals_overturned,
    CASE WHEN appeals_filed > 0
         THEN CAST(appeals_overturned AS REAL) / appeals_filed
    END AS overturn_rate
FROM issuer_totals
WHERE claims_received IS NOT NULL
  AND claims_denied   IS NOT NULL
  AND claims_received > 0;

-- Benchmarks are computed within a market, never across them. Dental (SADP)
-- plans deny at structurally different rates from medical (QHP) plans - annual
-- maximums, frequency limits, different benefit design - so a combined ranking
-- measures product type rather than issuer behaviour. Measured on PY2025 data:
-- dental median 30.3%, medical median 18.3%.
--
-- SQLite has no percentile function, so the median is the window-function form:
-- rank within the group, then average the middle one or two rows.
CREATE VIEW market_benchmarks AS
WITH ranked AS (
    SELECT
        primary_market,
        denial_rate,
        ROW_NUMBER() OVER (PARTITION BY primary_market ORDER BY denial_rate) AS position,
        COUNT(*)    OVER (PARTITION BY primary_market)                       AS issuers
    FROM issuer_denial_rates
)
SELECT
    primary_market,
    AVG(denial_rate) AS median_denial_rate,
    MAX(issuers)     AS issuer_count
FROM ranked
WHERE position IN ((issuers + 1) / 2, (issuers + 2) / 2)
GROUP BY primary_market;

-- State benchmark within a market, only where enough issuers exist to support
-- one. A median over three issuers is not a benchmark.
CREATE VIEW state_market_benchmarks AS
WITH ranked AS (
    SELECT
        primary_market,
        state,
        denial_rate,
        ROW_NUMBER() OVER (PARTITION BY primary_market, state ORDER BY denial_rate) AS position,
        COUNT(*)    OVER (PARTITION BY primary_market, state)                       AS issuers
    FROM issuer_denial_rates
)
SELECT
    primary_market,
    state,
    AVG(denial_rate) AS median_denial_rate,
    MAX(issuers)     AS issuer_count
FROM ranked
WHERE position IN ((issuers + 1) / 2, (issuers + 2) / 2)
GROUP BY primary_market, state
HAVING MAX(issuers) >= 5;

-- Why claims were denied, per issuer. Shares are computed against the sum of
-- the reason columns, not the headline denial count, because the two do not
-- always reconcile in the source data.
CREATE VIEW denial_reason_mix AS
SELECT
    plan_market,
    state,
    issuer_id,
    issuer_name,
    SUM(COALESCE(denied_referral_required, 0))       AS referral_required,
    SUM(COALESCE(denied_out_of_network, 0))          AS out_of_network,
    SUM(COALESCE(denied_service_excluded, 0))        AS service_excluded,
    SUM(COALESCE(denied_not_medically_necessary, 0)) AS not_medically_necessary,
    SUM(COALESCE(denied_behavioral_health, 0))       AS behavioral_health,
    SUM(COALESCE(denied_benefit_limit_reached, 0))   AS benefit_limit_reached,
    SUM(COALESCE(denied_member_not_covered, 0))      AS member_not_covered,
    SUM(COALESCE(denied_experimental, 0))            AS experimental,
    SUM(COALESCE(denied_administrative, 0))          AS administrative,
    SUM(COALESCE(denied_other, 0))                   AS other_reason
FROM plan_claims
GROUP BY plan_market, state, issuer_id, issuer_name;
