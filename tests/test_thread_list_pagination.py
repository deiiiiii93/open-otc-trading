"""Thread list paging + server-side search.

The list is bounded (20 by default) so its cost stops scaling with history —
an unbounded list with every message eagerly loaded is what exhausted the
connection pool. Search therefore has to run SERVER-side: filtering the loaded
page on the client would silently only search what happened to be fetched.
"""
from datetime import datetime, timedelta


def _seed(session, *, count: int, source: str = "desk", titles=None):
    """Create `count` threads, oldest first, with deterministic updated_at."""
    from app.models import AgentThread

    base = datetime(2026, 1, 1, 12, 0, 0)
    made = []
    for n in range(count):
        title = titles[n] if titles and n < len(titles) else f"thread {n:03d}"
        thread = AgentThread(
            title=title,
            character="trader",
            source=source,
            created_at=base + timedelta(minutes=n),
            updated_at=base + timedelta(minutes=n),
        )
        session.add(thread)
        made.append(thread)
    session.commit()
    return made


def _add_message(session, thread, content: str):
    from app.models import AgentMessage

    session.add(AgentMessage(thread_id=thread.id, role="user", content=content))
    session.commit()


def _titles(response):
    assert response.status_code == 200, response.text
    return [t["title"] for t in response.json()]


def test_list_threads_returns_twenty_newest_by_default(client, session):
    _seed(session, count=25)

    listed = client.get("/api/chat/threads", params={"source": "desk"})

    titles = _titles(listed)
    assert len(titles) == 20
    assert titles[0] == "thread 024"  # newest first
    assert titles[-1] == "thread 005"


def test_offset_returns_the_following_page(client, session):
    _seed(session, count=25)

    page_two = client.get(
        "/api/chat/threads", params={"source": "desk", "offset": 20}
    )

    assert _titles(page_two) == [
        "thread 004", "thread 003", "thread 002", "thread 001", "thread 000",
    ]


def test_limit_is_honoured(client, session):
    _seed(session, count=25)

    listed = client.get("/api/chat/threads", params={"source": "desk", "limit": 5})

    assert len(_titles(listed)) == 5


def test_limit_above_the_ceiling_is_rejected(client, session):
    """An unbounded list is the defect; a caller must not be able to ask for one."""
    listed = client.get("/api/chat/threads", params={"source": "desk", "limit": 5000})

    assert listed.status_code == 422


def test_search_finds_a_thread_far_beyond_the_first_page(client, session):
    """The whole point: search spans the DB, not the loaded page."""
    titles = [f"thread {n:03d}" for n in range(40)]
    titles[0] = "needle in the oldest thread"  # oldest => page 2 of an unsearched list
    _seed(session, count=40, titles=titles)

    found = client.get(
        "/api/chat/threads", params={"source": "desk", "q": "needle"}
    )

    assert _titles(found) == ["needle in the oldest thread"]


def test_search_matches_message_content_not_just_titles(client, session):
    threads = _seed(session, count=3)
    _add_message(session, threads[0], "the counterparty asked about AAPL vega")

    found = client.get("/api/chat/threads", params={"source": "desk", "q": "vega"})

    assert _titles(found) == [threads[0].title]


def test_search_is_case_insensitive(client, session):
    _seed(session, count=2, titles=["Snowball pricing", "other"])

    found = client.get("/api/chat/threads", params={"source": "desk", "q": "SNOWBALL"})

    assert _titles(found) == ["Snowball pricing"]


def test_search_stays_inside_the_requested_source(client, session):
    """Search must not leak arena threads onto a desk-scoped list."""
    _seed(session, count=1, titles=["desk needle"])
    _seed(session, count=1, source="arena", titles=["arena needle"])

    found = client.get("/api/chat/threads", params={"source": "desk", "q": "needle"})

    assert _titles(found) == ["desk needle"]


def test_search_treats_sql_wildcards_as_literal_text(client, session):
    """`%` and `_` are LIKE wildcards; a desk user typing them means the character."""
    _seed(session, count=2, titles=["100% hedged", "100 hedged"])

    found = client.get("/api/chat/threads", params={"source": "desk", "q": "100%"})

    assert _titles(found) == ["100% hedged"]


def test_search_results_are_paged_too(client, session):
    """A broad query must not re-open the unbounded payload."""
    _seed(session, count=25, titles=[f"needle {n:03d}" for n in range(25)])

    found = client.get("/api/chat/threads", params={"source": "desk", "q": "needle"})

    assert len(_titles(found)) == 20
