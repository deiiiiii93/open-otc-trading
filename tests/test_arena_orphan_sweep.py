"""A match that is KILLED never runs its `finally` purge, so the portfolios it
created leak forever: every later match takes its baseline ABOVE them, and the
`id > baseline` guard can never re-catch them.

Measured 2026-09-24: run #115 was SIGKILLed mid high-board match and left a
"Board Review" view (id 9103, source [9101]). Portfolio ids are reused, and every
high-board match re-seeds "Desk Control Book" as 9101 -- so the stale view pointed
at a fresh, legitimate-looking book for five weeks, and gpt-6-luna@high reused it
instead of calling create_portfolio. The pre-match sweep reclaims such rows.

Ownership proof: an ARENA thread's create_portfolio span minted THIS id at THIS
row's created_at. The timestamp replaces the lost baseline -- and is what keeps
an innocent row that later reused the id from being deleted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import models


def _thread(session, source="arena"):
    t = models.AgentThread(title="t", source=source)
    session.add(t)
    session.commit()
    return t


def _view(session, name="Board Review"):
    p = models.Portfolio(name=name, kind="view", tags=[])
    session.add(p)
    session.commit()
    return p


def _span(pid, thread_id, at):
    return {"portfolio_id": pid, "thread_id": thread_id,
            "start_time": at.replace(tzinfo=timezone.utc).isoformat()}


def _sweep(monkeypatch, spans):
    from app.services.arena import runner
    monkeypatch.setattr(runner, "collect_portfolio_creations",
                        lambda store=None: spans)
    return runner._sweep_orphaned_match_portfolios()


def test_a_portfolio_minted_by_an_arena_thread_is_reclaimed(session, monkeypatch):
    thread = _thread(session)
    leak = _view(session)
    leak_id = leak.id

    assert _sweep(monkeypatch, [_span(leak_id, thread.id, leak.created_at)]) == [leak_id]

    session.expire_all()
    assert session.get(models.Portfolio, leak_id) is None


def test_a_non_arena_thread_creation_is_never_touched(session, monkeypatch):
    """A desk user's own create_portfolio is real work, not arena debris."""
    thread = _thread(session, source="chat")
    real = _view(session, name="My Desk View")

    assert _sweep(monkeypatch, [_span(real.id, thread.id, real.created_at)]) == []
    assert session.get(models.Portfolio, real.id) is not None


def test_a_reused_id_is_not_mistaken_for_the_leak(session, monkeypatch):
    """The arena thread minted this id LONG ago and its row was purged; the id was
    later reused by an unrelated row. Same id, different moment -> not owned."""
    thread = _thread(session)
    reused = _view(session, name="Control Desk Portfolio")
    minted_long_ago = reused.created_at - timedelta(days=3)

    assert _sweep(monkeypatch, [_span(reused.id, thread.id, minted_long_ago)]) == []
    assert session.get(models.Portfolio, reused.id) is not None


def test_the_sweep_never_raises(monkeypatch):
    """Hygiene must never block a match (same contract as the finally purges)."""
    from app.services.arena import runner

    def boom(store=None):
        raise RuntimeError("trace store down")

    monkeypatch.setattr(runner, "collect_portfolio_creations", boom)
    assert runner._sweep_orphaned_match_portfolios() == []


def test_run_match_sweeps_before_seeding():
    """Wired, not just unit-tested: the sweep must run in run_match, BEFORE the
    seeded purge -- a unit test alone passes even if nothing ever calls it."""
    import inspect

    from app.services.arena import runner

    src = inspect.getsource(runner.run_match)
    assert "_sweep_orphaned_match_portfolios()" in src
    assert src.index("_sweep_orphaned_match_portfolios()") < src.index(
        "_purge_seeded_portfolios(session")
