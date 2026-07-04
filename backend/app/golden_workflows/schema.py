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

    @model_validator(mode="after")
    def _exactly_one_comparator(self) -> "_ToolResultPath":
        comps = [self.equals is not None, self.gte is not None,
                 self.lte is not None, self.is_not_null is not None]
        if sum(comps) != 1:
            raise ValueError("tool_result_path needs exactly one comparator")
        return self


class _ToolNotCalled(BaseModel):
    type: Literal["tool_not_called"]
    name: str


class _ArtifactContains(BaseModel):
    type: Literal["artifact_contains"]
    kind: str
    any_of: list[str] = Field(min_length=1)


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


Assertion = Annotated[
    Union[_SkillRouted, _SkillsRoutedSequence, _ToolsRoutedSequence, _ToolCalled,
          _TaskReturnedId, _ArtifactExists, _ResponseContains, _ToolResultPath,
          _ToolNotCalled, _ArtifactContains, _ResponseQuotesToolValue],
    Field(discriminator="type"),
]


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
    persona: Literal["trader", "risk_manager", "sales", "quant", "high_board"]
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    fixtures: str
    tags: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    success: Success
    narration: list[str] = Field(default_factory=list)  # attached by loader

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
