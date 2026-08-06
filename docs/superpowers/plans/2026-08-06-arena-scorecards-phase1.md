# Arena Per-Model Scorecards (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate ten per-model Arena scorecards from the live run #94 data and prepare
the repo's front door, so outreach to Chinese LLM labs can go out in week 1.

**Architecture:** A pure rendering kernel in `backend/app/services/arena/scorecard.py`
takes plain dicts (leaderboard row, target config, failed checks) and returns markdown —
no DB, no IO, fully unit-testable. A thin CLI in `scripts/render_scorecards.py` does the
DB reads and file writes. Target metadata (lab, tier, channels, anomaly note) lives in a
checked-in YAML config, not in code, so a future run only needs a config edit. All tests
run against a JSON fixture captured from the live DB in Task 1, so the suite stays
hermetic.

**Tech Stack:** Python 3.11, SQLAlchemy (read-only), PyYAML, pytest. No new dependencies.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-08-06-otc-agent-public-narrative-design.md`.

- **Accuracy rule (§5.3):** where a low score has already been diagnosed and fixed, say
  so. Reporting a known-and-fixed harness fault as an open question is dishonest and
  destroys the credibility the whole plan rests on.
- **GLM 5.2 specifically (§5.3):** the run #94 board row is the **clean re-run after the
  `protocol: anthropic` pin**. Its SYN 0/5 is a real result and must never be rendered as
  a suspected harness fault.
- **Never lead with velocity (§2):** no LOC counts, no commit counts, no "built in 7
  weeks" in any generated card or README copy.
- **Etiquette (§5.5):** cards contain data and one question. Never a pitch. **No mention
  of wanting a job or consulting work.**
- **Fail-honest (repo convention, `store._derive_card`):** a missing card, a missing
  transcript, or a missing tool count renders as an explicit absence with a machine
  reason. Never fabricate a number to fill a slot.
- Board run is **#94**; banked per-model runs are **#85–#93**; live DB is
  `data/open_otc.sqlite3`. Read-only — this plan never writes to it.

## Scope

**This plan covers Phase 1 (§5) and the front door (§9) only.** Phase 2 (flagship essay),
Phase 3 (Arena engine + Paper B) and Phase 4 (whitepaper) are independently deliverable
and each needs its own plan. Do not begin them here.

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/fixtures/arena_run94_leaderboard.json` | **Create.** Captured live leaderboard output; makes every later test hermetic |
| `tests/fixtures/arena_run94_breakdowns.json` | **Create.** Per-model `score_breakdown` blobs for trace extraction tests |
| `docs/arena/scorecards/targets.yaml` | **Create.** The ten lab targets: model id, lab, tier, channels, anomaly note |
| `backend/app/services/arena/scorecard.py` | **Create.** Pure kernel: target loading, failed-check extraction, markdown rendering |
| `scripts/render_scorecards.py` | **Create.** CLI: DB read → kernel → write markdown files |
| `docs/arena/scorecards/*.md` | **Generated.** Ten scorecards, one per lab |
| `tests/test_arena_scorecard.py` | **Create.** Unit tests for the kernel |
| `README.md` | **Modify.** Add the "Engineering notes" door (§9) |
| `CHANGELOG.md` | **Modify.** Required by the pre-push hook for any backend change |

---

### Task 1: Capture live fixtures and pin the card-derivation path

The stored `score_breakdown` for run #94 has **no `diagnosis` key**, so calling
`store._derive_card` on the top-level blob returns `(None, "missing_tool_count")`. Cards
must come from `store.leaderboard()`, which derives per-trial cards from `bd["aggregate"]`
and averages them. This task proves that empirically and freezes the result as a fixture
so no later task depends on the live DB.

**Files:**
- Create: `tests/fixtures/arena_run94_leaderboard.json`
- Create: `tests/fixtures/arena_run94_breakdowns.json`

**Interfaces:**
- Consumes: nothing.
- Produces: two JSON fixtures. `arena_run94_leaderboard.json` is a `list[dict]`, each row
  having at least `model_id: str`, `mean_objective: float`, `match_count: int`, and
  `card_mean: dict | None` where the card dict has keys
  `OVR, GRD, ADH, SYN, EFF, PRC, CON` (ints 0–99). `arena_run94_breakdowns.json` is a
  `dict[str, dict]` mapping `model_id` → the raw `score_breakdown`.

- [ ] **Step 1: Write the capture script**

Create `/tmp/capture_arena_fixtures.py`:

```python
"""One-off: freeze run #94 leaderboard + breakdowns as test fixtures."""
import json, sys
from pathlib import Path

sys.path.insert(0, "backend")
from app import database
from app.services.arena import store

REPO = Path(__file__).resolve().parent
OUT = Path("tests/fixtures")
OUT.mkdir(parents=True, exist_ok=True)

with database.SessionLocal() as session:
    rows = store.leaderboard(session, run_id=94)
    (OUT / "arena_run94_leaderboard.json").write_text(
        json.dumps(rows, indent=2, default=str), encoding="utf-8")

    import sqlite3
    c = sqlite3.connect("data/open_otc.sqlite3")
    bds = {m: json.loads(b) for m, b in
           c.execute("select model_id, score_breakdown from arena_match where run_id=94")}
    (OUT / "arena_run94_breakdowns.json").write_text(
        json.dumps(bds, indent=2), encoding="utf-8")

print(f"rows={len(rows)} breakdowns={len(bds)}")
print("carded:", sum(1 for r in rows if r.get("card_mean")))
print("sample:", json.dumps(rows[0], indent=2, default=str)[:600])
```

- [ ] **Step 2: Run it and inspect the output**

Run: `.venv/bin/python /tmp/capture_arena_fixtures.py`

Expected: `rows=18 breakdowns=18`, and `carded:` should print `18`.

**If `carded: 0`** — the derive-on-read path is not producing cards for this run. Stop and
report; do not proceed to Task 2. The whole plan assumes cards are derivable, and a
fabricated card would violate the fail-honest constraint. The fallback (compute stats
directly from `objective.axes` via `scoring.card_from_axes`) is a design change that needs
sign-off, not a silent workaround.

- [ ] **Step 3: Verify the ten target models are all present**

Run:

```bash
.venv/bin/python -c "
import json
rows = json.load(open('tests/fixtures/arena_run94_leaderboard.json'))
have = {r['model_id'] for r in rows}
want = {'deepseek-v4-flash','deepseek-v4-pro','qwen-3-7-max','longcat-2-0',
        'step-3-7-flash','hunyuan-3','kimi-2-7','mimo-2-5','mimo-2-5-pro',
        'doubao-seed-2-1-pro','glm-5-2','minimax-m3'}
print('missing:', want - have)
"
```

Expected: `missing: set()`

- [ ] **Step 4: Commit the fixtures**

```bash
git add tests/fixtures/arena_run94_leaderboard.json tests/fixtures/arena_run94_breakdowns.json
git commit -m "test(arena): freeze run #94 leaderboard and breakdowns as scorecard fixtures

Captured from the live DB so the scorecard generator's tests stay hermetic.
Cards come from store.leaderboard (per-trial derive + average), not from
_derive_card on the folded top-level breakdown, which carries no diagnosis
block and would return missing_tool_count.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Target configuration and loader

**Files:**
- Create: `docs/arena/scorecards/targets.yaml`
- Create: `backend/app/services/arena/scorecard.py`
- Test: `tests/test_arena_scorecard.py`

**Interfaces:**
- Consumes: Task 1's `arena_run94_leaderboard.json`.
- Produces: `load_targets(path: Path) -> list[Target]`, where `Target` is a frozen
  dataclass with fields `model_ids: tuple[str, ...]`, `lab: str`, `tier: str`
  (`"A" | "B1" | "B2"`), `channels: tuple[str, ...]`, `anomaly: str | None`,
  `interop_note: bool`. Later tasks call `load_targets` and read these fields.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_scorecard.py`:

```python
"""Tests for the Arena per-model scorecard generator (pure, no DB)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.arena.scorecard import Target, load_targets

REPO = Path(__file__).resolve().parents[1]
TARGETS = REPO / "docs/arena/scorecards/targets.yaml"
FIXTURES = REPO / "tests/fixtures"


@pytest.fixture
def leaderboard() -> list[dict]:
    return json.loads((FIXTURES / "arena_run94_leaderboard.json").read_text())


def test_loads_ten_targets():
    targets = load_targets(TARGETS)
    assert len(targets) == 10


def test_every_target_model_exists_on_the_board(leaderboard):
    board = {r["model_id"] for r in leaderboard}
    for t in load_targets(TARGETS):
        for mid in t.model_ids:
            assert mid in board, f"{t.lab}: {mid} is not on run #94"


def test_no_model_is_claimed_by_two_targets():
    seen: set[str] = set()
    for t in load_targets(TARGETS):
        for mid in t.model_ids:
            assert mid not in seen, f"{mid} claimed twice"
            seen.add(mid)


def test_tiers_are_valid_and_glm_is_not_b2_only():
    targets = {t.lab: t for t in load_targets(TARGETS)}
    assert {t.tier for t in targets.values()} <= {"A", "B1", "B2"}
    # Accuracy rule: GLM's harness fault was found and fixed, so it carries the
    # interop note rather than an open "is my harness broken?" question.
    assert targets["Zhipu"].interop_note is True


def test_interop_note_set_for_exactly_the_four_protocol_pinned_labs():
    labs = {t.lab for t in load_targets(TARGETS) if t.interop_note}
    assert labs == {"Zhipu", "Alibaba", "Meituan", "MiniMax"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.arena.scorecard'`

- [ ] **Step 3: Write the targets config**

Create `docs/arena/scorecards/targets.yaml`:

```yaml
# Phase 1 outreach targets — see docs/superpowers/specs/2026-08-06-otc-agent-public-narrative-design.md §5.3
# tier: A  = strong result, good news, send first
#       B1 = interop finding (protocol:anthropic), highest-value message
#       B2 = anomalous result, harness suspected, send last
targets:
  - lab: DeepSeek
    tier: A
    model_ids: [deepseek-v4-flash, deepseek-v4-pro]
    channels: ["https://github.com/deepseek-ai/DeepSeek-V3/issues", "zhihu"]
    anomaly: null
    interop_note: false

  - lab: Alibaba
    tier: A
    model_ids: [qwen-3-7-max]
    channels: ["https://github.com/QwenLM/Qwen/issues", "zhihu"]
    anomaly: null
    interop_note: true

  - lab: Meituan
    tier: A
    model_ids: [longcat-2-0]
    channels: ["https://github.com/meituan-longcat/LongCat-Flash-Chat/issues"]
    anomaly: null
    interop_note: true

  - lab: StepFun
    tier: A
    model_ids: [step-3-7-flash]
    channels: ["https://github.com/stepfun-ai/Step-3/issues"]
    anomaly: null
    interop_note: false

  - lab: Zhipu
    tier: B1
    model_ids: [glm-5-2]
    channels: ["https://github.com/zai-org/GLM-4.5/issues"]
    anomaly: >-
      Synthesis scored 0/5 on the clean re-run. Reported as a real result, NOT as a
      suspected harness fault: the earlier broken rows were diagnosed and re-run after
      the protocol pin.
    interop_note: true

  - lab: MiniMax
    tier: B1
    model_ids: [minimax-m3]
    channels: ["https://github.com/MiniMax-AI/MiniMax-M2/issues"]
    anomaly: >-
      The only model on the board scoring below 92 on adherence (78).
    interop_note: true

  - lab: Tencent
    tier: B2
    model_ids: [hunyuan-3]
    channels: ["https://github.com/Tencent-Hunyuan/Hunyuan-A13B/issues"]
    anomaly: >-
      Synthesis 40 against adherence 99 — the model follows the process but does not
      produce the deliverable. That split is more consistent with a harness or output
      -parsing fault than with capability, and I would like help ruling that out.
    interop_note: false

  - lab: Moonshot
    tier: B2
    model_ids: [kimi-2-7]
    channels: ["https://github.com/MoonshotAI/Kimi-K2/issues"]
    anomaly: >-
      Synthesis 40 with consistency 33 — the deliverable appears on some trials and not
      others, which reads as instability rather than inability.
    interop_note: false

  - lab: Xiaomi
    tier: B2
    model_ids: [mimo-2-5, mimo-2-5-pro]
    channels: ["https://github.com/XiaomiMiMo/MiMo/issues"]
    anomaly: >-
      MiMo 2.5 Pro ranks below non-Pro MiMo 2.5 on this workflow. An inverted tier order
      is usually worth a look on the serving side.
    interop_note: false

  - lab: ByteDance
    tier: B2
    model_ids: [doubao-seed-2-1-pro]
    channels: ["https://github.com/volcengine/verl/issues"]
    anomaly: >-
      Consistency 0 against an objective score of 82.8 — high capability with no
      run-to-run reproducibility, which is the pattern I would most expect to be my
      harness rather than the model.
    interop_note: false
```

- [ ] **Step 4: Write the minimal loader**

Create `backend/app/services/arena/scorecard.py`:

```python
"""Per-model Arena scorecards for lab outreach.

Pure kernel: every function here takes plain dicts and returns strings or
dataclasses. No DB, no filesystem beyond reading the checked-in targets config,
so the whole module is unit-testable against frozen fixtures.

The CLI that supplies the DB rows is `scripts/render_scorecards.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

VALID_TIERS = frozenset({"A", "B1", "B2"})


@dataclass(frozen=True)
class Target:
    """One outreach recipient — a lab, and the models of theirs we scored."""

    lab: str
    tier: str
    model_ids: tuple[str, ...]
    channels: tuple[str, ...]
    anomaly: str | None
    interop_note: bool


def load_targets(path: Path) -> list[Target]:
    """Parse the checked-in targets config. Raises on an invalid tier."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    targets = []
    for entry in raw["targets"]:
        tier = entry["tier"]
        if tier not in VALID_TIERS:
            raise ValueError(f"{entry['lab']}: unknown tier {tier!r}")
        targets.append(
            Target(
                lab=entry["lab"],
                tier=tier,
                model_ids=tuple(entry["model_ids"]),
                channels=tuple(entry.get("channels") or ()),
                anomaly=(entry.get("anomaly") or None),
                interop_note=bool(entry.get("interop_note")),
            )
        )
    return targets
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -v`
Expected: 5 passed.

If `test_every_target_model_exists_on_the_board` fails, a model id in the YAML does not
match the DB. Fix the YAML to match the DB — never the reverse.

- [ ] **Step 6: Commit**

```bash
git add docs/arena/scorecards/targets.yaml backend/app/services/arena/scorecard.py tests/test_arena_scorecard.py
git commit -m "feat(arena): scorecard target config and loader

Ten Phase 1 outreach targets with tier, channels and anomaly framing, validated
against the frozen run #94 board so a target can never name a model we did not
score.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Extract failed checks from a stored breakdown

Each card needs two or three *specific* failing checks, named to the check that failed.
`objective.steps[].checks[]` carries them, with `objective.success[]` holding the
session-level assertions.

**Files:**
- Modify: `backend/app/services/arena/scorecard.py`
- Test: `tests/test_arena_scorecard.py`

**Interfaces:**
- Consumes: `Target` from Task 2; `arena_run94_breakdowns.json` from Task 1.
- Produces: `failed_checks(breakdown: dict, limit: int = 3) -> list[FailedCheck]` where
  `FailedCheck` is a frozen dataclass with `label: str`, `axis: str`, `step: str | None`.
  Task 5 renders these.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_scorecard.py`:

```python
from app.services.arena.scorecard import FailedCheck, failed_checks


@pytest.fixture
def breakdowns() -> dict:
    return json.loads((FIXTURES / "arena_run94_breakdowns.json").read_text())


def test_failed_checks_returns_at_most_limit(breakdowns):
    got = failed_checks(breakdowns["glm-5-2"], limit=3)
    assert len(got) <= 3
    assert all(isinstance(c, FailedCheck) for c in got)


def test_failed_checks_finds_glm_synthesis_failures(breakdowns):
    """GLM scored synthesis 0/5, so at least one synthesis check must surface."""
    got = failed_checks(breakdowns["glm-5-2"], limit=10)
    assert any(c.axis == "synthesis" for c in got)


def test_failed_checks_empty_for_a_perfect_board_row(breakdowns):
    """Gemini 3.6 Flash scored 100.0 objective — nothing should be reported failed."""
    assert failed_checks(breakdowns["gemini-3-6-flash"]) == []


def test_failed_checks_tolerates_a_breakdown_with_no_steps():
    assert failed_checks({"objective": {}}) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -k failed_checks -v`
Expected: FAIL — `ImportError: cannot import name 'FailedCheck'`

- [ ] **Step 3: Implement**

Append to `backend/app/services/arena/scorecard.py`:

```python
@dataclass(frozen=True)
class FailedCheck:
    """One assertion the model did not satisfy, named for the outreach card."""

    label: str
    axis: str
    step: str | None


def _is_failed(check: dict) -> bool:
    """A check is failed when it explicitly reports not passing.

    Fail-honest: a check with no `passed` key is unknown, not failed — reporting
    an unknown as a failure to a model's authors would be exactly the kind of
    fabricated claim the accuracy rule forbids.
    """
    return check.get("passed") is False


def failed_checks(breakdown: dict, limit: int = 3) -> list[FailedCheck]:
    """Collect up to `limit` failed checks, step checks first, then session ones."""
    objective = breakdown.get("objective") or {}
    out: list[FailedCheck] = []

    for step in objective.get("steps") or []:
        step_name = step.get("id") or step.get("title")
        for check in step.get("checks") or []:
            if _is_failed(check):
                out.append(FailedCheck(
                    label=str(check.get("label") or check.get("type") or "unlabelled"),
                    axis=str(check.get("axis") or "unknown"),
                    step=str(step_name) if step_name else None,
                ))

    for check in objective.get("success") or []:
        if _is_failed(check):
            out.append(FailedCheck(
                label=str(check.get("label") or check.get("type") or "unlabelled"),
                axis=str(check.get("axis") or "unknown"),
                step=None,
            ))

    return out[:limit]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -v`
Expected: 9 passed.

If `test_failed_checks_finds_glm_synthesis_failures` fails, inspect the real key names:

```bash
.venv/bin/python -c "
import json
bd = json.load(open('tests/fixtures/arena_run94_breakdowns.json'))['glm-5-2']
step = (bd['objective'].get('steps') or [{}])[0]
print('step keys:', list(step))
print('check sample:', json.dumps((step.get('checks') or [{}])[0], indent=2))
"
```

Adjust `_is_failed` and the key lookups to the real shape. Do **not** loosen `_is_failed`
to treat a missing key as failure.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/scorecard.py tests/test_arena_scorecard.py
git commit -m "feat(arena): extract failed checks from a stored score breakdown

Step checks first, then session assertions, capped at a limit. A check with no
explicit passed=False is treated as unknown rather than failed, so a card never
reports a fabricated failure to a model's authors.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Resolve per-model transcripts, degrading honestly

Transcript availability is **not uniform**. Runs #85–#91 hold real per-model transcripts;
runs #92 and #94 are folded boards whose `transcript_path` is `None`. DeepSeek ×2 and
Doubao have no transcript on the banked runs. A card for those models must say so rather
than link a path that does not exist.

**Files:**
- Modify: `backend/app/services/arena/scorecard.py`
- Test: `tests/test_arena_scorecard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `resolve_transcript(paths_by_model: dict[str, str | None], model_id: str,
  repo_root: Path) -> str | None` — returns a repo-relative path string only when the file
  exists on disk, else `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_scorecard.py`:

```python
from app.services.arena.scorecard import resolve_transcript


def test_resolve_transcript_returns_none_when_path_is_none(tmp_path):
    assert resolve_transcript({"m": None}, "m", tmp_path) is None


def test_resolve_transcript_returns_none_when_model_absent(tmp_path):
    assert resolve_transcript({}, "missing-model", tmp_path) is None


def test_resolve_transcript_returns_none_when_file_missing(tmp_path):
    paths = {"m": "artifacts/arena/85/wf/m/transcript.json"}
    assert resolve_transcript(paths, "m", tmp_path) is None


def test_resolve_transcript_returns_path_when_file_exists(tmp_path):
    rel = "artifacts/arena/85/wf/m/transcript.json"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert resolve_transcript({"m": rel}, "m", tmp_path) == rel
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -k resolve_transcript -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_transcript'`

- [ ] **Step 3: Implement**

Append to `backend/app/services/arena/scorecard.py`:

```python
def resolve_transcript(
    paths_by_model: dict[str, str | None],
    model_id: str,
    repo_root: Path,
) -> str | None:
    """Return the repo-relative transcript path only if the file really exists.

    Folded board runs store `transcript_path=None`, and some models have no
    banked transcript at all. Returning None lets the card state the absence
    instead of linking a dangling pointer.
    """
    rel = paths_by_model.get(model_id)
    if not rel:
        return None
    return rel if (Path(repo_root) / rel).is_file() else None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/scorecard.py tests/test_arena_scorecard.py
git commit -m "feat(arena): resolve per-model transcripts with honest absence

Folded board runs carry transcript_path=None and some models have no banked
transcript, so the resolver returns None unless the file exists on disk. The
card then states the absence rather than linking a dangling pointer.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Render one scorecard to markdown

**Files:**
- Modify: `backend/app/services/arena/scorecard.py`
- Test: `tests/test_arena_scorecard.py`

**Interfaces:**
- Consumes: `Target` (Task 2), `FailedCheck` (Task 3).
- Produces: `render_scorecard(*, target: Target, rows: list[dict], checks: dict[str,
  list[FailedCheck]], transcripts: dict[str, str | None], run_id: int) -> str`.
  `rows` are the leaderboard rows for this target's models; `checks` and `transcripts`
  are keyed by model id. Task 6 calls this once per target.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_arena_scorecard.py`:

```python
from app.services.arena.scorecard import render_scorecard


def _target(**kw) -> Target:
    base = dict(lab="Zhipu", tier="B1", model_ids=("glm-5-2",),
                channels=("https://example.invalid/issues",), anomaly=None,
                interop_note=False)
    base.update(kw)
    return Target(**base)


def _rows(model_id="glm-5-2"):
    return [{"model_id": model_id, "mean_objective": 62.9, "match_count": 2,
             "card_mean": {"OVR": 64, "GRD": 70, "ADH": 99, "SYN": 0,
                           "EFF": 64, "PRC": 58, "CON": 99}}]


def test_render_includes_the_card_stats():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={}, run_id=94)
    for stat in ("OVR", "GRD", "ADH", "SYN", "EFF", "PRC", "CON"):
        assert stat in md
    assert "64" in md


def test_render_asks_the_serving_question():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={}, run_id=94)
    assert "serve" in md.lower()


def test_render_never_pitches():
    """Etiquette rule: no job or consulting ask, ever."""
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={}, run_id=94).lower()
    for banned in ("hiring", "consult", "opportunit", "resume", "cv",
                   "lines of code", "commits"):
        assert banned not in md


def test_interop_note_rendered_only_when_flagged():
    plain = render_scorecard(target=_target(interop_note=False), rows=_rows(),
                             checks={}, transcripts={}, run_id=94)
    flagged = render_scorecard(target=_target(interop_note=True), rows=_rows(),
                               checks={}, transcripts={}, run_id=94)
    assert "empty-string" not in plain
    assert "empty-string" in flagged


def test_missing_transcript_states_the_absence():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={"glm-5-2": None}, run_id=94)
    assert "not banked" in md.lower()


def test_missing_card_is_reported_not_fabricated():
    rows = [{"model_id": "glm-5-2", "mean_objective": 62.9, "match_count": 2,
             "card_mean": None}]
    md = render_scorecard(target=_target(), rows=rows, checks={},
                          transcripts={}, run_id=94)
    assert "not available" in md.lower()
    assert "OVR 0" not in md
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -k render -v`
Expected: FAIL — `ImportError: cannot import name 'render_scorecard'`

- [ ] **Step 3: Implement**

Append to `backend/app/services/arena/scorecard.py`:

```python
_STATS = ("OVR", "GRD", "ADH", "SYN", "EFF", "PRC", "CON")

_ENVIRONMENT = """\
The environment is a live OTC derivatives trading desk, not a static prompt set. Each
model drives the production orchestrator end to end with no human in the loop — reading
risk, pricing a portfolio, running scenarios, and producing a governance report, where
each step consumes the previous step's output. Scoring reads the system's own trace log
against a fixed objective manifest; there is no LLM judge in these numbers.\
"""

_INTEROP = """\
### One interoperability finding you may want

Through an OpenAI-compatible gateway, this model emitted tool calls with **empty-string
ids**, which caused every persona delegation to fail before it started. Pinning the route
to the Anthropic wire protocol resolved it, and the board row above is the clean re-run
after that pin. Four models on this board needed the same pin, so it may be worth checking
against your own OpenAI-compatible surface.\
"""

_QUESTION = """\
### My question

Did I serve your model correctly? The configuration is listed above. If any of it is
wrong — the wire protocol, a limit, the way tools are presented — I would rather fix the
harness and re-run than publish a number that measures my plumbing instead of your model.\
"""


def _card_table(rows: list[dict]) -> str:
    lines = ["| Model | " + " | ".join(_STATS) + " | Objective |",
             "|---" * (len(_STATS) + 2) + "|"]
    for row in rows:
        card = row.get("card_mean")
        if not card:
            lines.append(f"| `{row['model_id']}` | "
                         + " | ".join("n/a" for _ in _STATS)
                         + f" | {row.get('mean_objective', 'n/a')} |")
            continue
        lines.append(f"| `{row['model_id']}` | "
                     + " | ".join(str(card.get(s, "n/a")) for s in _STATS)
                     + f" | {row.get('mean_objective', 'n/a')} |")
    return "\n".join(lines)


def render_scorecard(
    *,
    target: Target,
    rows: list[dict],
    checks: dict[str, list[FailedCheck]],
    transcripts: dict[str, str | None],
    run_id: int,
) -> str:
    """Render one lab's outreach card. Pure — no IO."""
    parts: list[str] = [
        f"# {target.lab} — OTC Desk Agent Arena, Run #{run_id}",
        "",
        _ENVIRONMENT,
        "",
        "## Result",
        "",
        _card_table(rows),
        "",
        "Stats are scaled 0–99. GRD grounding, ADH adherence, SYN synthesis, "
        "EFF efficiency against a calibrated par, PRC precision, CON consistency "
        "across trials. OVR is their weighted combination; CON is reported, not "
        "folded into OVR.",
        "",
    ]

    if any(r.get("card_mean") is None for r in rows):
        parts += ["> An ability card is **not available** for a row above: the stored "
                  "breakdown lacked the evidence needed to derive one. The objective "
                  "score still stands.", ""]

    if target.interop_note:
        parts += [_INTEROP, ""]

    if target.anomaly:
        parts += ["### What stands out", "", target.anomaly.strip(), ""]

    failing = [(mid, c) for mid, cs in checks.items() for c in cs]
    if failing:
        parts += ["### Specific checks that did not pass", ""]
        for mid, check in failing:
            where = f", step `{check.step}`" if check.step else ""
            parts.append(f"- `{mid}` — **{check.label}** ({check.axis}{where})")
        parts.append("")

    parts += ["### Evidence", ""]
    for model_id in target.model_ids:
        path = transcripts.get(model_id)
        if path:
            parts.append(f"- `{model_id}` — full trace: `{path}`")
        else:
            parts.append(f"- `{model_id}` — per-trial trace **not banked** for this "
                         f"board row; the score derives from the stored breakdown.")
    parts += ["", _QUESTION, ""]
    return "\n".join(parts)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py -v`
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/scorecard.py tests/test_arena_scorecard.py
git commit -m "feat(arena): render a per-model outreach scorecard to markdown

Card table, environment description, optional interop note, failing checks, and
the serving question. A test pins the etiquette rule: no hiring or consulting
ask and no velocity claims may appear in a generated card.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: CLI — read the DB and write the ten cards

**Files:**
- Create: `scripts/render_scorecards.py`
- Generated: `docs/arena/scorecards/*.md`

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces: ten markdown files at `docs/arena/scorecards/<lab-slug>.md`.

- [ ] **Step 1: Write the CLI**

Create `scripts/render_scorecards.py`:

```python
#!/usr/bin/env python3
"""Render per-lab Arena outreach scorecards from the live DB.

Usage:
    .venv/bin/python scripts/render_scorecards.py --run 94

Read-only against data/open_otc.sqlite3. Writes docs/arena/scorecards/<lab>.md.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import database  # noqa: E402
from app.services.arena import store  # noqa: E402
from app.services.arena.scorecard import (  # noqa: E402
    failed_checks, load_targets, render_scorecard, resolve_transcript,
)

TARGETS = REPO / "docs/arena/scorecards/targets.yaml"
OUT_DIR = REPO / "docs/arena/scorecards"
BANKED = range(85, 94)


def _slug(lab: str) -> str:
    return lab.lower().replace(" ", "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=94)
    ap.add_argument("--db", default=str(REPO / "data/open_otc.sqlite3"))
    args = ap.parse_args()

    with database.SessionLocal() as session:
        board = {r["model_id"]: r for r in store.leaderboard(session, run_id=args.run)}

    conn = sqlite3.connect(args.db)
    breakdowns = {m: json.loads(b) for m, b in conn.execute(
        "select model_id, score_breakdown from arena_match where run_id=?", (args.run,))}
    # Transcripts live on the banked per-model runs, not the folded board.
    paths = {m: p for m, p in conn.execute(
        "select model_id, transcript_path from arena_match "
        f"where run_id between {BANKED.start} and {BANKED.stop - 1} "
        "and transcript_path is not null")}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for target in load_targets(TARGETS):
        rows = [board[m] for m in target.model_ids if m in board]
        if not rows:
            print(f"SKIP {target.lab}: no board rows", file=sys.stderr)
            continue
        checks = {m: failed_checks(breakdowns.get(m) or {}) for m in target.model_ids}
        transcripts = {m: resolve_transcript(paths, m, REPO) for m in target.model_ids}
        md = render_scorecard(target=target, rows=rows, checks=checks,
                              transcripts=transcripts, run_id=args.run)
        (OUT_DIR / f"{_slug(target.lab)}.md").write_text(md, encoding="utf-8")
        written += 1
        print(f"wrote {_slug(target.lab)}.md  tier={target.tier}  "
              f"models={','.join(target.model_ids)}  "
              f"traces={sum(1 for v in transcripts.values() if v)}/{len(transcripts)}")

    print(f"\n{written}/10 cards written to {OUT_DIR.relative_to(REPO)}")
    return 0 if written == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python scripts/render_scorecards.py --run 94`
Expected: ten `wrote …` lines, then `10/10 cards written to docs/arena/scorecards`.

- [ ] **Step 3: Read all ten cards end to end**

Run: `cat docs/arena/scorecards/*.md | less`

This is a **manual review gate, not a formality.** These go to real engineers under your
name. Check specifically:

- The GLM card reports SYN 0/5 as a real result and does **not** call it a suspected
  harness fault (Global Constraints, accuracy rule).
- No card contains a pitch, a job mention, or a velocity claim.
- Every claimed trace path exists; every absence is stated.
- The four interop cards are Zhipu, Alibaba, Meituan, MiniMax — and nobody else.

- [ ] **Step 4: Commit**

```bash
git add scripts/render_scorecards.py docs/arena/scorecards/
git commit -m "feat(arena): CLI to render the ten Phase 1 outreach scorecards

Reads the folded board for cards and the banked per-model runs #85-93 for
transcripts, then writes one markdown card per lab.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Front door — README engineering-notes door and CHANGELOG

Spec §9: anyone arriving from any channel currently lands on a product pitch rather than
evidence of judgment.

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Add the Engineering notes section to README**

Insert immediately **after** the `### Key Features` list and **before** the next `##`
heading. Do not touch the existing overview or screenshots.

```markdown
### Engineering notes

The desk is the substrate; these are the notes from building it.

- **[Agent guidance file](CLAUDE.md)** — the working `CLAUDE.md` this codebase is actually
  developed against: the failure modes, the invariants, and the gotchas that cost real
  debugging time. Most such files are private; this one is not.
- **[The OTC Desk Agent Arena](docs/arena/)** — repeated-trial evaluation of ~18 LLMs
  driving this desk end to end with no human in the loop, scored from the system's own
  trace log. Five published runs, including an audit that found 15 of 50 of our own checks
  carried no ability signal — and that correcting them reordered the board.
```

- [ ] **Step 2: Verify the links resolve**

Run:

```bash
.venv/bin/python -c "
import pathlib, re
readme = pathlib.Path('README.md').read_text()
sec = readme.split('### Engineering notes')[1].split('\n##')[0]
for link in re.findall(r']\(([^)]+)\)', sec):
    p = pathlib.Path(link)
    print(('OK  ' if p.exists() else 'DEAD'), link)
"
```

Expected: both lines print `OK`.

- [ ] **Step 3: Update CHANGELOG**

Under `## [Unreleased]`, add to the `### Added` subsection (create it if absent):

```markdown
- **Arena per-model scorecards** — `scripts/render_scorecards.py` renders one outreach
  card per lab from a board run, combining the derived ability card, the failing checks,
  and the banked per-model trace. Targets and framing live in
  `docs/arena/scorecards/targets.yaml`; the rendering kernel
  (`backend/app/services/arena/scorecard.py`) is pure and tested against frozen run #94
  fixtures.
- **README engineering-notes section** — surfaces `CLAUDE.md` and the Arena reports from
  the front page.
```

- [ ] **Step 4: Run the full arena test suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_arena_scorecard.py tests/test_arena_scoring.py -v`
Expected: all pass. Never pipe pytest through `tail` — the summary line hides which test
failed.

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: add the engineering-notes door to the README

Surfaces CLAUDE.md and the Arena reports from the front page, so a reader
arriving from an outreach link lands on the engineering record rather than the
product pitch.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (Phase 1 + §9):**

| Spec requirement | Task |
|---|---|
| §5.1 ten cards, generated not hand-written | 6 |
| §5.1 card contents (card, environment, traces, config, question, links) | 5 |
| §5.2 the serving question | 5 (pinned by `test_render_asks_the_serving_question`) |
| §5.3 tiers A / B1 / B2 and target list | 2 |
| §5.3 GLM accuracy rule | 2 (config), 6 step 3 (manual gate) |
| §5.3 interop finding for exactly four labs | 2 (pinned by test), 5 (rendering) |
| §5.4 channels per lab | 2 (`channels` field) |
| §5.5 etiquette — no pitch | 5 (pinned by `test_render_never_pitches`) |
| §9 front door | 7 |
| §2 no velocity bragging | 5 (pinned by `test_render_never_pitches`) |

**Not covered here, by design:** §5.6 success measurement, §6 essay, §7 Arena engine and
Paper B, §8 whitepaper, §13 arXiv probe. Each needs its own plan; the arXiv probe (§13.1)
is a 20-minute manual action requiring no code.

**Known risk carried into execution:** Task 1 Step 2 is a hard gate. If
`store.leaderboard(run_id=94)` returns uncarded rows, the plan stops rather than
fabricating stats — resolving it is a design decision, not an implementation detail.

**Type consistency:** `Target` (Task 2) is consumed by Tasks 5 and 6 with identical field
names. `FailedCheck` (Task 3) is consumed by Task 5 as `checks: dict[str,
list[FailedCheck]]`. `resolve_transcript` (Task 4) returns `str | None`, consumed by
Task 5 as `transcripts: dict[str, str | None]`. `render_scorecard` is keyword-only in both
its definition and its Task 6 call site.
