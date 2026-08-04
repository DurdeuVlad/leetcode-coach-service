from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from sqlmodel import Session, select

from leetcode_coach_v2.db.models import V2Problem
from leetcode_coach_v2.domain.exceptions import DomainError
from leetcode_coach_v2.domain.schemas import (
    HydratedCandidate,
    ProposalPreview,
    ProposalSelection,
    normalise_mix,
)


def hydrate_proposal(
    session: Session,
    selections: Sequence[ProposalSelection],
    *,
    required_mix: Mapping[str, int] | None = None,
    expected_count: int | None = None,
) -> ProposalPreview:
    """Validate model-selected slugs and hydrate every display field from DB.

    This is the canonical trust boundary: titles, URLs, tags and difficulty
    from a model are intentionally not accepted by this function.
    """

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
    ineligible = [slug for slug in slugs if by_slug[slug].solved or not by_slug[slug].eligible]
    if ineligible:
        raise DomainError(f"ineligible problem slug: {ineligible[0]}")

    actual_mix = Counter(
        getattr(by_slug[slug].difficulty, "value", by_slug[slug].difficulty) for slug in slugs
    )
    mix = normalise_mix(required_mix)
    if mix is not None and dict(actual_mix) != {key: value for key, value in mix.items() if value}:
        raise DomainError(f"wrong proposal difficulty mix: got {dict(actual_mix)}, expected {mix}")

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
