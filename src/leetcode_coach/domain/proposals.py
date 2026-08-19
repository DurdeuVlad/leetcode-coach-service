from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlmodel import Session, select

from leetcode_coach.db.models import V2Problem
from leetcode_coach.domain.exceptions import DomainError
from leetcode_coach.domain.schemas import (
    HydratedCandidate,
    ProposalPreview,
    ProposalSelection,
)


def hydrate_proposal(
    session: Session,
    selections: Sequence[ProposalSelection],
    *,
    required_mix: Mapping[str, int] | None = None,
    expected_count: int | None = None,
) -> ProposalPreview:
    """Validate selected identities and hydrate display fields from durable rows."""

    if expected_count is not None and len(selections) != expected_count:
        raise DomainError(f"proposal must contain exactly {expected_count} selections")
    slugs = [item.slug.strip() for item in selections]
    if not slugs or any(not slug for slug in slugs):
        raise DomainError("proposal selections require non-empty slugs")
    if len(set(slugs)) != len(slugs):
        raise DomainError("proposal contains duplicate slugs")

    problems = session.exec(select(V2Problem).where(V2Problem.slug.in_(slugs))).all()
    by_slug = {problem.slug: problem for problem in problems}
    unknown = [slug for slug in slugs if slug not in by_slug]
    if unknown:
        raise DomainError(f"unknown canonical problem slug: {unknown[0]}")
    # Solved state, pool eligibility, count, and difficulty mix are coaching
    # decisions. This boundary retains only identity and rendering invariants.
    del required_mix

    return ProposalPreview(
        tuple(
            HydratedCandidate(
                position=index,
                slug=selection.slug.strip(),
                title=by_slug[selection.slug.strip()].title,
                url=by_slug[selection.slug.strip()].url,
                difficulty=str(
                    getattr(
                        by_slug[selection.slug.strip()].difficulty,
                        "value",
                        by_slug[selection.slug.strip()].difficulty,
                    )
                ),
                tags=by_slug[selection.slug.strip()].tags,
                reasoning=selection.reasoning.strip(),
                coaching_hint=selection.coaching_hint.strip(),
            )
            for index, selection in enumerate(selections, start=1)
        )
    )
