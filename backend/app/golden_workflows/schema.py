from __future__ import annotations
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class WorkflowError(Exception): ...
class DuplicateWorkflowError(WorkflowError): ...
class FixturePathError(WorkflowError): ...
class MissingReplayError(WorkflowError): ...
class NarrationMismatchError(WorkflowError): ...
class UnknownToolError(WorkflowError): ...
class ToolNameCollisionError(WorkflowError): ...
class SkillNameCollisionError(WorkflowError): ...
class UnresolvedSeedRefError(WorkflowError): ...
class UnknownSeedNamespaceError(WorkflowError): ...
class DuplicateAliasError(WorkflowError): ...
class UnresolvedAliasError(WorkflowError): ...
class SeedIdConflictError(WorkflowError): ...


class UnusedReplayWarning(UserWarning): ...


def normalize_tool_name(name: str) -> str:
    return name[:-5] if name.endswith("_tool") else name


def normalize_skill(name: str) -> str:
    return name.strip().lower()


# --- assertion union ---
class _SkillRouted(BaseModel):
    type: Literal["skill_routed"]
    name: str


class _SkillsRoutedSequence(BaseModel):
    type: Literal["skills_routed_sequence"]
    names: list[str]


class _ToolsRoutedSequence(BaseModel):
    type: Literal["tools_routed_sequence"]
    names: list[str]


class _ToolCalled(BaseModel):
    type: Literal["tool_called"]
    name: str
    args: dict | None = None
    args_any_of: list[dict] | None = None
    exclusive_keys: list[str] | None = None
    # Exact-use mode: EVERY call of `name` must match a candidate (and there
    # must be at least one call). Without it a compliant first call would mask
    # a later over-executing/substituted call of the same tool.
    all_calls: bool = False
    # Cap on the number of calls of `name` (None = unlimited). For stateful /
    # costly dispatch tools, even duplicate COMPLIANT calls are over-execution.
    max_calls: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _args_exclusive(self) -> "_ToolCalled":
        if self.args is not None and self.args_any_of is not None:
            raise ValueError("tool_called: args and args_any_of are mutually exclusive")
        if self.args_any_of is not None and not self.args_any_of:
            raise ValueError("tool_called: args_any_of must be non-empty")
        return self


class _TaskReturnedId(BaseModel):
    type: Literal["task_returned_id"]
    tool: str


class _ArtifactExists(BaseModel):
    type: Literal["artifact_exists"]
    kind: str


class _ResponseContains(BaseModel):
    type: Literal["response_contains"]
    any_of: list[str]


class _ToolResultPath(BaseModel):
    type: Literal["tool_result_path"]
    tool: str
    path: str
    equals: Any | None = None
    gte: float | None = None
    lte: float | None = None
    is_not_null: Literal[True] | None = None
    # step (default) reads only THIS step's results; session reads cumulatively
    # over steps 0..i, crediting a model that obtained the evidence earlier and
    # answered correctly from it rather than redundantly re-calling the tool.
    # Without this field a manifest's `scope: session` was silently ignored
    # (extra=ignore), so the opt-in was unreachable.
    scope: Literal["step", "session"] = "step"
    # Tolerance band for a NUMERIC equals (e.g. an engine-computed delta that
    # absorbs maturity-encoding variance). Optional; absent → exact _exact compare.
    rel_tol: float | None = None

    @model_validator(mode="after")
    def _exactly_one_comparator(self) -> "_ToolResultPath":
        comps = [self.equals is not None, self.gte is not None,
                 self.lte is not None, self.is_not_null is not None]
        if sum(comps) != 1:
            raise ValueError("tool_result_path needs exactly one comparator")
        if self.rel_tol is not None:
            if self.equals is None or isinstance(self.equals, bool) \
                    or not isinstance(self.equals, (int, float)):
                raise ValueError("tool_result_path: rel_tol requires a numeric equals")
            if not (0 < self.rel_tol < 1):
                raise ValueError("tool_result_path: rel_tol must be in (0, 1)")
        return self


class _ToolNotCalled(BaseModel):
    type: Literal["tool_not_called"]
    name: str
    # Probe exemption: a call whose args subset-match one candidate is NOT a
    # violation. A trap step's graded sin is SUBSTITUTION (running something the
    # user did not name); probing the exact requested-but-absent referent and
    # taking the system's own "not found" error is honest verification and must
    # not score like silently running `inflation_shock` instead. exclusive_keys
    # blocks a matching call that also carries another listed carrier key (a
    # probe smuggling an invented `custom` grid in the same call). Near-miss
    # name variants deliberately do NOT match — subset matching is exact on
    # values — because spelling hunts are brute-forcing, not verification.
    # Absent → every call is a violation, exactly the historical semantics.
    except_args_any_of: list[dict] | None = None
    exclusive_keys: list[str] | None = None

    @model_validator(mode="after")
    def _exemption_shape(self) -> "_ToolNotCalled":
        if self.except_args_any_of is not None and not self.except_args_any_of:
            raise ValueError(
                "tool_not_called: except_args_any_of must be non-empty when present")
        if self.exclusive_keys is not None and self.except_args_any_of is None:
            raise ValueError(
                "tool_not_called: exclusive_keys requires except_args_any_of")
        return self


_AXES = {"procedural", "adherence", "grounding", "synthesis"}


class _ArtifactContains(BaseModel):
    type: Literal["artifact_contains"]
    kind: str
    any_of: list[str] = Field(min_length=1)
    # Optional per-assertion axis override (else the global _AXIS_BY_TYPE default).
    # Used to score ticket-CONTENT checks on the synthesis axis.
    axis: str | None = None

    @model_validator(mode="after")
    def _axis_ok(self) -> "_ArtifactContains":
        if self.axis is not None and self.axis not in _AXES:
            raise ValueError(f"axis must be one of {_AXES}")
        return self


class _ResponseQuotesToolValue(BaseModel):
    type: Literal["response_quotes_tool_value"]
    tool: str
    path: str
    rel_tol: float = 0.02
    scope: Literal["step", "session"] = "step"
    match: Literal["signed", "magnitude"] = "signed"
    near: list[str] | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "_ResponseQuotesToolValue":
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        if self.near is not None and not self.near:
            raise ValueError("near must be non-empty when present")
        return self


class _ResponseQuotesValue(BaseModel):
    # Grounding against a KNOWN-TRUTH fixture value (Spec A harvest), independent
    # of whether the tool fired this turn — credits correct-from-context answers.
    type: Literal["response_quotes_value"]
    value: float
    rel_tol: float = 0.02
    scope: Literal["step", "session"] = "step"
    match: Literal["signed", "magnitude"] = "signed"
    near: list[str] | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "_ResponseQuotesValue":
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        if self.near is not None and not self.near:
            raise ValueError("near must be non-empty when present")
        return self


class _AnswerFieldEquals(BaseModel):
    # Categorical grounding/adherence against a typed answer the model commits via
    # record_answer — verifies role (this field IS the hotspot), not mere presence.
    type: Literal["answer_field_equals"]
    field: str = Field(min_length=1)
    equals: str | None = None
    any_of: list[str] | None = None
    # Null comparator: is_null=True passes iff the field was RECORDED with a
    # null value (an unrecorded field still fails — the model must commit the
    # absence, not merely omit it). Lets a trap grade "no run happened" as a
    # structured answer instead of a lexical phrase list.
    is_null: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> "_AnswerFieldEquals":
        comparators = sum(
            x is not None for x in (self.equals, self.any_of, self.is_null))
        if comparators != 1:
            raise ValueError(
                "answer_field_equals: exactly one of equals/any_of/is_null")
        if self.any_of is not None and not self.any_of:
            raise ValueError("answer_field_equals: any_of must be non-empty")
        return self


class _AnswerFieldQuotes(BaseModel):
    # Numeric grounding against a typed answer field — direct value compare (no text
    # scan, no proximity heuristic); the key binds the number to its role.
    type: Literal["answer_field_quotes"]
    field: str = Field(min_length=1)
    value: float
    rel_tol: float = 0.02
    match: Literal["signed", "magnitude"] = "signed"

    @model_validator(mode="after")
    def _bounds(self) -> "_AnswerFieldQuotes":
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        return self


class _ToolResultRatio(BaseModel):
    # Spot- AND multiplier-invariant grounding: value = dig(numer) / (dig(denom) *
    # dig(denom_mult if set else 1)), read from the last matching tool RESULT
    # (source=result) or tool CALL args (source=call). Lets grounding survive the
    # live arena's real market fetch — e.g. premium/(spot*contract_multiplier).
    type: Literal["tool_result_ratio"]
    tool: str
    numer: str = Field(min_length=1)
    denom: str = Field(min_length=1)
    denom_mult: str | None = None
    equals: float
    rel_tol: float = 0.02
    scope: Literal["step", "session"] = "step"
    source: Literal["result", "call"] = "result"

    @model_validator(mode="after")
    def _bounds(self) -> "_ToolResultRatio":
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        return self


class _AssertionAnyOf(BaseModel):
    # Composite: scores as ONE check, passes iff any member passes. Expresses an
    # "either competent path" ground (e.g. a trap the model may refuse two ways)
    # that independent AND-ed assertions cannot. Carries an explicit axis.
    type: Literal["assertion_any_of"]
    axis: str
    any_of: list["Assertion"] = Field(min_length=2)

    @model_validator(mode="after")
    def _axis_ok(self) -> "_AssertionAnyOf":
        if self.axis not in _AXES:
            raise ValueError(f"axis must be one of {_AXES}")
        return self


Assertion = Annotated[
    Union[_SkillRouted, _SkillsRoutedSequence, _ToolsRoutedSequence, _ToolCalled,
          _TaskReturnedId, _ArtifactExists, _ResponseContains, _ToolResultPath,
          _ToolNotCalled, _ArtifactContains, _ResponseQuotesToolValue,
          _ResponseQuotesValue, _AnswerFieldEquals, _AnswerFieldQuotes,
          _ToolResultRatio, _AssertionAnyOf],
    Field(discriminator="type"),
]

# _AssertionAnyOf.any_of forward-references the Assertion union above.
_AssertionAnyOf.model_rebuild()


class ToolExpectation(BaseModel):
    name: str
    args: dict | None = None


class Success(BaseModel):
    assertions: list[Assertion] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)


class Step(BaseModel):
    user: str = Field(min_length=1)
    # None (explicit YAML null) = no skill-routing point for this step. Used where
    # skills_routed is structurally blind: the runtime never re-reads an
    # already-loaded SKILL.md, so repeat-skill steps can never pass the check.
    expected_skill: str | None
    expected_tools: list[ToolExpectation] = Field(default_factory=list)
    outcome: str = Field(min_length=1)
    assertions: list[Assertion] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)
    replay: str


class GoldenWorkflow(BaseModel):
    id: str
    schema_version: Literal[1]
    # CONTENT version, bumped by hand on any scoring-relevant edit and stamped onto
    # every arena run (services/arena/provenance.py). Scores from different
    # manifest versions are not comparable; the stamped content hash catches the
    # edit nobody bumped. schema_version above is the FORMAT, not the content.
    manifest_version: int = Field(default=1, ge=1)
    persona: Literal["trader", "risk_manager", "sales", "quant", "high_board"]
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    fixtures: str
    tags: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    success: Success
    narration: list[str] = Field(default_factory=list)  # attached by loader
    # Benchmark-reserved scenario-set names that MUST NOT exist in the live
    # scenario library — the runner asserts their absence at match setup so a
    # "does-not-exist" trap step cannot silently invert (a competent model that
    # checks the library and reports "not found" must be able to pass).
    trap_absent_sets: list[str] = Field(default_factory=list)
    # Designed complete-run tool-call count for the EFF ability stat (Spec B).
    # Optional so existing manifests still load; when absent, scoring.designed_par
    # derives it from sum(len(step.expected_tools)). Overridable per-workflow.
    par_tool_calls: int | None = Field(default=None, ge=1)
    # Pinned accounting date for live matches (ISO, e.g. "2025-07-16"). When set,
    # the arena runner passes it to stream_and_persist so the agent's Accounting
    # anchor is a CONCLUDED trading day — a live fetch_market_snapshot on the
    # anchor then always returns data (Run #26: anchoring on the real run date let
    # models query a not-yet-concluded US session and stall on empty windows).
    # Optional; unset workflows keep the real current date as the anchor.
    accounting_date: str | None = None
    # Routing policy for the document-extraction sub-call inside
    # parse_trade_confirmation. Unset (default) = the production tag ladder
    # (confirmation_extractor -> fast -> registry default), unchanged — which is
    # what every board through #132 ran on.
    #
    # "contestant" = the arena routes extraction to the MATCH's own model. That is
    # what makes a vision check measure the CONTESTANT rather than whichever model
    # happens to hold the confirmation_extractor tag; without it every model on the
    # board reads every document with one shared model's eyes, so the check lands
    # N/N across the field and carries zero ability signal while still occupying
    # the denominator (the Run #58 audit's defect, in 15 of 50 checks).
    #
    # Declared in the MANIFEST rather than inferred by the harness so the
    # experimental arm is predeclared and no other workflow is silently rerouted.
    # A routing POLICY, never a model id: pinning a specific model here would
    # un-level the very board it scores.
    extractor_model: Literal["contestant"] | None = None
    # Model capabilities this workflow REQUIRES, matched against the registry
    # model's declared tags at LAUNCH. A list rather than a boolean so a second
    # capability costs nothing later. Empty (default) = runnable by any model.
    requires: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", v):
            raise ValueError("id must be a kebab slug")
        return v


# Convert pydantic ValidationError → WorkflowError at the model boundary used by the loader.
def parse_workflow(data: dict) -> GoldenWorkflow:
    try:
        return GoldenWorkflow(**data)
    except ValidationError as e:
        raise WorkflowError(str(e)) from e
