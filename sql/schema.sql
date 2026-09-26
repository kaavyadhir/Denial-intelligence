-- Issuer- and plan-level claims, denials and appeals from the CMS
-- Transparency in Coverage PUF.
--
-- The source workbook types every numeric column as text and uses blanks,
-- "N/A" and suppression markers for missing values, so the loader coerces and
-- the schema stores real INTEGERs with NULL for "not reported". That
-- distinction matters: a NULL denial count is unknown, a 0 is a claim that
-- nobody denied, and averaging them together would be wrong.

DROP TABLE IF EXISTS plan_claims;

CREATE TABLE plan_claims (
    id                          INTEGER PRIMARY KEY,
    plan_market                 TEXT    NOT NULL,   -- which sheet the row came from
    state                       TEXT    NOT NULL,
    issuer_id                   TEXT    NOT NULL,
    issuer_name                 TEXT    NOT NULL,
    plan_id                     TEXT,
    plan_type                   TEXT,
    metal_level                 TEXT,

    -- issuer-level totals (repeated across that issuer's plan rows in the source)
    issuer_claims_received_in_network   INTEGER,
    issuer_claims_denied_in_network     INTEGER,
    issuer_claims_received_out_network  INTEGER,
    issuer_claims_denied_out_network    INTEGER,

    -- plan-level totals
    plan_claims_received_in_network     INTEGER,
    plan_claims_denied_in_network       INTEGER,

    -- denial reasons, plan level
    denied_referral_required            INTEGER,
    denied_out_of_network               INTEGER,
    denied_service_excluded             INTEGER,
    denied_not_medically_necessary      INTEGER,
    denied_behavioral_health            INTEGER,
    denied_benefit_limit_reached        INTEGER,
    denied_member_not_covered           INTEGER,
    denied_experimental                 INTEGER,
    denied_administrative               INTEGER,
    denied_other                        INTEGER,

    -- appeals, issuer level
    internal_appeals_filed              INTEGER,
    internal_appeals_overturned         INTEGER,
    external_appeals_filed              INTEGER,
    external_appeals_overturned         INTEGER,

    average_monthly_enrollment          INTEGER
);

CREATE INDEX idx_plan_claims_issuer ON plan_claims (issuer_id);
CREATE INDEX idx_plan_claims_state  ON plan_claims (state);
