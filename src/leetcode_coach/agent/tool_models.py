"""Strict, bounded schemas exposed to the Terra function tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ProposalSelection


class StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemPoolFilters(StrictToolModel):
    difficulty: list[Literal["easy", "medium", "hard"]] = Field(default_factory=list, max_length=3)
    include_tags: list[str] = Field(default_factory=list, max_length=10)
    exclude_tags: list[str] = Field(default_factory=list, max_length=10)
    topic: str | None = Field(default=None, max_length=80)


class AttemptHistoryFilters(StrictToolModel):
    problem_slug: str | None = Field(default=None, max_length=200)
    outcome: Literal["solved", "reviewed", "saw_solution", "attempted", "skipped"] | None = None
    include_reversed: bool = False


class CoachingMemoryUpdate(StrictToolModel):
    goals: str | list[str] | None = Field(default=None, max_length=100)
    preferences: str | list[str] | None = Field(default=None, max_length=100)
    availability: str | list[str] | None = Field(default=None, max_length=100)
    curriculum: str | list[str] | None = Field(default=None, max_length=100)
    mastery: str | list[str] | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=4000)

    def payload(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)


class ProposalSelectionInput(StrictToolModel):
    slug: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    difficulty: Literal["easy", "medium", "hard"]
    tags: str = Field(default="", max_length=1_000)
    reasoning: str = Field(min_length=1, max_length=800)
    coaching_hint: str = Field(min_length=1, max_length=800)

    def payload(self) -> ProposalSelection:
        return {
            "slug": self.slug,
            "title": self.title,
            "difficulty": self.difficulty,
            "tags": self.tags,
            "reasoning": self.reasoning,
            "coaching_hint": self.coaching_hint,
        }


class LessonDelta(StrictToolModel):
    lesson_id: int | None = Field(
        default=None,
        ge=1,
        description="Existing database lesson ID; null when creating a new lesson.",
    )
    title: str | None = Field(
        default=None,
        max_length=300,
        description="Required for a new lesson; optional for an existing lesson ID.",
    )
    category: str | None = Field(default=None, max_length=100)
    reinforcement_delta: int = Field(default=0, ge=-1, le=1)
    status: Literal["active", "graduated"] | None = None
    note: str | None = Field(default=None, max_length=1_000)

    def payload(self) -> dict[str, str | int | None]:
        return self.model_dump(exclude_none=True)


class SolEvidenceItem(StrictToolModel):
    source: str = Field(min_length=1, max_length=100)
    details: str = Field(min_length=1, max_length=4_000)


class SolAdvisorRequest(StrictToolModel):
    objective: str = Field(min_length=1, max_length=1_500)
    evidence: list[SolEvidenceItem] = Field(default_factory=list, max_length=10)
    constraints: str = Field(min_length=1, max_length=1_500)
    uncertainty: str = Field(min_length=1, max_length=1_500)

    def evidence_payload(self) -> dict[str, str]:
        return {item.source: item.details for item in self.evidence}
