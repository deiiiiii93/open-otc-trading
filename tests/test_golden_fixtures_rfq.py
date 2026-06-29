"""Tests for the rfqs seed namespace + optional positions.rfq FK in
golden_workflows.fixtures."""
from app.golden_workflows.fixtures import FixtureBundle, apply_seed
from app import models


def _seed_only(seed: dict) -> FixtureBundle:
    return FixtureBundle(seed=seed, replay={})


def test_rfq_namespace_seeds_row(session):
    bundle = _seed_only({
        "rfqs": [{"alias": "r1", "status": "submitted", "client_name": "ARENA Client"}],
    })
    ids = apply_seed(bundle, session)
    rid = ids["rfqs"]["r1"]
    row = session.get(models.RFQ, rid)
    assert row.status == "submitted"
    assert row.client_name == "ARENA Client"


def test_position_links_seeded_rfq(session):
    bundle = _seed_only({
        "portfolios": [{"alias": "p", "name": "T1 Portfolio"}],
        "rfqs": [{"alias": "r1", "status": "submitted"}],
        "positions": [{
            "alias": "pos1", "portfolio": "p", "rfq": "r1",
            "underlying": "MSFT", "product_type": "EuropeanVanillaOption", "quantity": 1,
        }],
    })
    ids = apply_seed(bundle, session)
    pos = session.get(models.Position, ids["positions"]["pos1"])
    assert pos.rfq_id == ids["rfqs"]["r1"]


def test_position_without_rfq_still_seeds(session):
    # The positions.rfq FK is OPTIONAL: a position with no rfq must still validate+seed.
    bundle = _seed_only({
        "portfolios": [{"alias": "p", "name": "T2 Portfolio"}],
        "positions": [{
            "alias": "pos1", "portfolio": "p",
            "underlying": "MSFT", "product_type": "EuropeanVanillaOption", "quantity": 1,
        }],
    })
    ids = apply_seed(bundle, session)
    pos = session.get(models.Position, ids["positions"]["pos1"])
    assert pos.rfq_id is None
