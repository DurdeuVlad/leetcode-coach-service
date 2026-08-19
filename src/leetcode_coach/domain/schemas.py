from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposalSelection:
    """Problem identity and coaching copy selected by the agent."""

    slug: str
    reasoning: str = ""
    coaching_hint: str = ""
    title: str | None = None
    difficulty: str | None = None
    tags: str = ""


@dataclass(frozen=True)
class HydratedCandidate:
    position: int
    slug: str
    title: str
    url: str
    difficulty: str
    tags: str
    reasoning: str
    coaching_hint: str


@dataclass(frozen=True)
class ProposalPreview:
    candidates: tuple[HydratedCandidate, ...]

    def as_dict(self) -> dict[str, object]:
        return {"candidates": [candidate.__dict__ for candidate in self.candidates]}
