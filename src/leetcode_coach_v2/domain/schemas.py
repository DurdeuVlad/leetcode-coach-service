from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ProposalSelection:
    """The only problem fields an LLM is permitted to provide."""

    slug: str
    reasoning: str = ""
    coaching_hint: str = ""


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


def normalise_mix(required_mix: Mapping[str, int] | None) -> dict[str, int] | None:
    if required_mix is None:
        return None
    return {str(key).lower(): int(value) for key, value in required_mix.items()}
