"""Confirmation batches created by a match must be purged.

`_delete_portfolios_with_dependents` finds dependents by scanning for columns
literally named `portfolio_id` / `position_id`, then recursing over foreign keys
-- but the recursion explicitly SKIPS the portfolios table itself.
`ConfirmationBatch.default_portfolio_id` is an FK to `portfolios` under a
different column name, so a batch parsed into the arena book is never swept and
the portfolio delete dies on a foreign-key constraint.

`ExtractedTrade.booked_position_id` IS reachable (the recursion descends from
`positions`), so the leak is exactly one table wide -- and fatal.
"""
from __future__ import annotations

import pytest

from app import models


def _portfolio(session, name="Arena Confirmation Desk"):
    p = models.Portfolio(name=name, tags=["arena"])
    session.add(p)
    session.commit()
    return p


def test_the_dependents_sweep_cannot_reach_a_confirmation_batch(session):
    """Characterization: this is the bug, proven before relying on the fix.

    If this ever starts passing, the sweep learned to follow the FK and
    _purge_match_confirmations may be redundant -- check before deleting it.
    """
    from app.services.arena.runner import _delete_portfolios_with_dependents

    portfolio = _portfolio(session)
    session.add(models.ConfirmationBatch(source="agent",
                                         default_portfolio_id=portfolio.id))
    session.commit()

    with pytest.raises(Exception):
        _delete_portfolios_with_dependents(session, [portfolio.id])
        session.commit()


def test_purge_removes_batches_created_above_the_baseline(session, monkeypatch):
    from app.services.arena import runner

    old = models.ConfirmationBatch(source="agent")
    session.add(old)
    session.commit()
    baseline = old.id

    new = models.ConfirmationBatch(source="agent")
    session.add(new)
    session.commit()
    new_id = new.id

    monkeypatch.setattr(
        runner, "collect_confirmation_batch_ids_created",
        lambda tid, store=None: {new_id, baseline},
    )
    runner._purge_match_confirmations(thread_id=1, batch_id_baseline=baseline)

    session.expire_all()
    assert session.get(models.ConfirmationBatch, new_id) is None
    # The pre-existing batch is BELOW the baseline: harvested but not created by
    # this match, so it must survive -- the same "touched != created" rule that
    # keeps _purge_match_rfqs from deleting an RFQ the model merely quoted.
    assert session.get(models.ConfirmationBatch, baseline) is not None


def test_purge_cascades_documents_and_extracted_trades(session, monkeypatch):
    from app.services.arena import runner

    batch = models.ConfirmationBatch(source="agent")
    session.add(batch)
    session.commit()
    doc = models.ConfirmationDocument(
        batch_id=batch.id, filename="c.pdf", stored_path="/tmp/c.pdf",
        sha256="0" * 64, byte_len=1, mime="application/pdf",
    )
    session.add(doc)
    session.commit()
    trade = models.ExtractedTrade(document_id=doc.id, family="EuropeanVanillaOption")
    session.add(trade)
    session.commit()
    batch_id, doc_id, trade_id = batch.id, doc.id, trade.id

    monkeypatch.setattr(
        runner, "collect_confirmation_batch_ids_created",
        lambda tid, store=None: {batch_id},
    )
    runner._purge_match_confirmations(thread_id=1, batch_id_baseline=batch_id - 1)

    session.expire_all()
    assert session.get(models.ExtractedTrade, trade_id) is None
    assert session.get(models.ConfirmationDocument, doc_id) is None
    assert session.get(models.ConfirmationBatch, batch_id) is None


def test_a_purged_batch_lets_its_portfolio_delete_cleanly(session, monkeypatch):
    """The whole point: after the purge, the portfolio sweep succeeds."""
    from app.services.arena import runner
    from app.services.arena.runner import _delete_portfolios_with_dependents

    portfolio = _portfolio(session, name="Arena Confirmation Desk 2")
    batch = models.ConfirmationBatch(source="agent",
                                     default_portfolio_id=portfolio.id)
    session.add(batch)
    session.commit()
    batch_id, pid = batch.id, portfolio.id

    monkeypatch.setattr(
        runner, "collect_confirmation_batch_ids_created",
        lambda tid, store=None: {batch_id},
    )
    runner._purge_match_confirmations(thread_id=1, batch_id_baseline=batch_id - 1)

    session.expire_all()
    _delete_portfolios_with_dependents(session, [pid])
    session.commit()
    assert session.get(models.Portfolio, pid) is None


def test_purge_never_raises(monkeypatch):
    """Cleanup is hygiene and must never mask a match failure."""
    from app.services.arena import runner

    def _boom(tid, store=None):
        raise RuntimeError("trace store down")

    monkeypatch.setattr(runner, "collect_confirmation_batch_ids_created", _boom)
    runner._purge_match_confirmations(thread_id=1, batch_id_baseline=0)
