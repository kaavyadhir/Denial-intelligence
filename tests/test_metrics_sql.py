"""The SQL metric layer, exercised against a tiny in-memory fixture.

Every number in the generated report comes out of these views, so their
arithmetic is tested directly rather than eyeballed on real data.
"""
import pytest

from app.db import initialise, query


@pytest.fixture
def db():
    import sqlite3
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialise(connection)
    return connection


def insert(connection, **overrides):
    row = {
        "plan_market": "Individual QHP", "state": "TX",
        "issuer_id": "11111", "issuer_name": "Acme Health",
        "plan_id": "P1", "plan_type": "HMO", "metal_level": "Silver",
        "issuer_claims_received_in_network": 10000,
        "issuer_claims_denied_in_network": 1200,
        "issuer_claims_received_out_network": None,
        "issuer_claims_denied_out_network": None,
        "plan_claims_received_in_network": None,
        "plan_claims_denied_in_network": None,
        "denied_referral_required": 0, "denied_out_of_network": 0,
        "denied_service_excluded": 0, "denied_not_medically_necessary": 0,
        "denied_behavioral_health": 0, "denied_benefit_limit_reached": 0,
        "denied_member_not_covered": 0, "denied_experimental": 0,
        "denied_administrative": 0, "denied_other": 0,
        "internal_appeals_filed": 100, "internal_appeals_overturned": 40,
        "external_appeals_filed": None, "external_appeals_overturned": None,
        "average_monthly_enrollment": 5000,
    }
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO plan_claims ({columns}) VALUES ({placeholders})", tuple(row.values())
    )
    connection.commit()


def test_denial_rate_is_denied_over_received(db):
    insert(db)
    rows = query(db, "SELECT * FROM issuer_denial_rates")
    assert rows[0]["denial_rate"] == pytest.approx(0.12)


def test_issuer_totals_do_not_multiply_by_plan_count(db):
    """Issuer figures repeat on every plan row. SUM would inflate them."""
    insert(db, plan_id="P1")
    insert(db, plan_id="P2")
    insert(db, plan_id="P3")

    rows = query(db, "SELECT * FROM issuer_totals")
    assert len(rows) == 1
    assert rows[0]["claims_received"] == 10000, "MAX, not SUM"
    assert rows[0]["plan_count"] == 3


def test_issuer_with_unreported_denominator_is_excluded_not_zeroed(db):
    insert(db, issuer_id="22222", issuer_claims_received_in_network=None)
    rows = query(db, "SELECT * FROM issuer_denial_rates WHERE issuer_id = '22222'")
    assert rows == [], "no denominator means no rate, not a rate of zero"


def test_zero_claims_received_does_not_divide_by_zero(db):
    insert(db, issuer_id="33333", issuer_claims_received_in_network=0,
           issuer_claims_denied_in_network=0)
    assert query(db, "SELECT * FROM issuer_denial_rates WHERE issuer_id = '33333'") == []


def test_overturn_rate_is_null_when_no_appeals_were_filed(db):
    insert(db, issuer_id="44444", internal_appeals_filed=0, internal_appeals_overturned=0)
    rows = query(db, "SELECT * FROM issuer_denial_rates WHERE issuer_id = '44444'")
    assert rows[0]["overturn_rate"] is None


def test_overturn_rate_is_overturned_over_filed(db):
    insert(db)
    rows = query(db, "SELECT * FROM issuer_denial_rates")
    assert rows[0]["overturn_rate"] == pytest.approx(0.40)


def test_market_median_with_an_odd_number_of_issuers(db):
    for n, (issuer, denied) in enumerate([("A", 1000), ("B", 2000), ("C", 3000)]):
        insert(db, issuer_id=f"1000{n}", issuer_name=issuer,
               issuer_claims_denied_in_network=denied)
    rows = query(db, "SELECT * FROM market_benchmarks")
    assert rows[0]["median_denial_rate"] == pytest.approx(0.20)
    assert rows[0]["issuer_count"] == 3


def test_market_median_with_an_even_number_of_issuers(db):
    for n, denied in enumerate([1000, 2000, 3000, 4000]):
        insert(db, issuer_id=f"2000{n}", issuer_name=f"I{n}",
               issuer_claims_denied_in_network=denied)
    rows = query(db, "SELECT * FROM market_benchmarks")
    assert rows[0]["median_denial_rate"] == pytest.approx(0.25), "mean of the middle two"


def test_denial_reasons_sum_across_an_issuers_plans(db):
    insert(db, plan_id="P1", denied_administrative=10, denied_experimental=5)
    insert(db, plan_id="P2", denied_administrative=20, denied_experimental=None)

    rows = query(db, "SELECT * FROM denial_reason_mix")
    assert rows[0]["administrative"] == 30
    assert rows[0]["experimental"] == 5, "NULL counts as absent, not as an error"


def test_an_issuer_in_several_markets_is_counted_once(db):
    """The PUF repeats issuer-wide figures into every market the issuer sells in.

    Blue Cross and Blue Shield of Alabama carries the same 13,033,751 claims
    into Individual QHP, Individual SADP and SHOP. Grouping by market would
    count it three times and compare its whole book against one market's peers.
    """
    insert(db, plan_market="Individual QHP", plan_id="Q1")
    insert(db, plan_market="Individual QHP", plan_id="Q2")
    insert(db, plan_market="SHOP", plan_id="S1")

    rows = query(db, "SELECT * FROM issuer_denial_rates")
    assert len(rows) == 1, "one row per issuer, not per market"
    assert rows[0]["claims_received"] == 10000, "not multiplied across markets"
    assert rows[0]["market_count"] == 2


def test_primary_market_is_where_the_issuer_has_most_plans(db):
    insert(db, plan_market="Individual QHP", plan_id="Q1")
    insert(db, plan_market="Individual QHP", plan_id="Q2")
    insert(db, plan_market="SHOP", plan_id="S1")

    rows = query(db, "SELECT * FROM issuer_denial_rates")
    assert rows[0]["primary_market"] == "Individual QHP"


def test_issuers_in_different_states_stay_separate(db):
    """HIOS issuer IDs are assigned per state, so state is part of the identity."""
    insert(db, state="TX", issuer_id="111")
    insert(db, state="FL", issuer_id="222")
    assert len(query(db, "SELECT * FROM issuer_denial_rates")) == 2
