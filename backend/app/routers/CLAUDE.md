# REST routers

Per-endpoint rules that are not obvious from the handler.

Part of [Open OTC Trading](../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

---

## The chat thread list is scoped, paged, and searched server-side

`GET /api/chat/threads` takes **`?source=`** (scope), **`?limit=`/`?offset=`**
(page; default 20, max 100) and **`?q=`** (search). Unscoped and unpaged, it
returned every public thread with every message eagerly loaded —
`public_thread_query` excludes only `hedge_evidence`, so **arena threads are
"public" too**, and an arena board mints one thread per match. On this desk that
was 671 of 719 threads and 59.7 MB of the ~61 MB payload; 3.5 s and 61 MB per call
became 0.04 s and 662 KB.

- **Search MUST stay server-side now that the list is a page.** The old desk
  filtered `thread.title + thread.messages[].content` on the client; against a
  paged list that silently searches only the rows that happened to be fetched,
  which looks like "no results" rather than "not loaded". `?q=` matches title or
  message content across every thread in scope (93 of 100 live hits were beyond
  page 1). It is debounced in the controller and paged like any other query, so a
  broad term cannot re-open the unbounded payload.
- **Escape LIKE wildcards.** `%` and `_` are metacharacters, so a desk user
  searching `100%` or `book_id` would otherwise match rows they never asked for
  (`_like_pattern` + `ilike(..., escape="\\")`).
- **Page ordering needs a tiebreaker.** `order_by(updated_at.desc(), id.desc())` —
  without the id, rows sharing an `updated_at` can repeat on one page and be
  skipped on the next.
- **The client pages by a growing window** (`limit` grows, `offset` stays 0)
  rather than appending at an offset, because the 4s async-task poll re-runs the
  same fetch: an append scheme would have the poll refresh only the first page.
  Scopes are fetched with the same limit and the merged list is trimmed back to
  it — exact, because the newest N of a union always lies inside the union of
  each list's newest N.
- **`withActiveThreadPreserved` is not defensive coding.** `activeThread` is
  derived from the list, so a search whose results omit the open thread would
  blank the message pane of the conversation being read.

- **A slow sync endpoint is a connection-pool bug waiting to happen.** `get_db`
  closes its session in a `finally` that runs *after* response serialization, so a
  handler holds its pooled connection for the whole render — here ~3.5 s of
  GIL-bound pydantic work, which does not parallelize, so concurrency multiplies
  hold times instead of overlapping them. FastAPI admits **40** concurrent sync
  handlers (anyio threadpool) while the engine admits **15** (SQLAlchemy 2.0
  defaults: `pool_size` 5 + `max_overflow` 10, `pool_timeout` 30 — `database.py`
  passes no pool arguments). Any handler slow enough for 15 to overlap ends in
  `QueuePool limit ... connection timed out`. Raising the pool only moves the
  threshold; the fix is to stop holding a connection through a big serialize.
- **Diagnosing it: count fds, don't read code first.** `QueuePool` retains only
  `pool_size` idle connections — overflow connections are *closed* on return — so
  `lsof -p <worker> | grep open_otc.sqlite3` showing exactly 15 means the pool is
  pinned at its ceiling. Check `%CPU`/state too: `R` at ~100% says CPU-bound
  serialization, not lock contention or a leaked session. Note `--reload` means
  the app runs in a **child** process; the parent holds no DB fds.
- **Client disconnect does not cancel a sync `def` endpoint.** Starlette runs it to
  completion in the threadpool, so an abandoned slow request keeps its connection
  and CPU — retries stack rather than replace, which is what turns a slow page into
  cascading 500s.
- **Adding a new thread `source` now needs a decision, not just a string.** The
  desk only sees what it asks for, so a new source is invisible there unless
  something requests it. `AgentThreadCreate.source` accepts any non-reserved value.
- **Per-thread resolution is deliberately NOT scoped.** `get_public_thread` /
  `_get_thread_or_404` share `public_thread_query`, and a thread must stay
  reachable by id whatever its source — only the *list* is scoped.
- The desk's "show arena threads" checkbox is **controlled by the controller**
  (`includeArena`), because it drives a second scoped fetch rather than filtering
  an already-downloaded payload. `AgentDesk`'s client-side arena filter stays: it
  keeps the list honest between toggling off and the re-fetch landing.
