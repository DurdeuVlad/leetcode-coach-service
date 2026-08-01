"""Idempotent fixture loader for the terminal simulator.

Seeds ``leetcode_problems`` + ``tutor_lessons`` so Flow A has an unsolved pool
to propose from and the coach pass has active lessons to match against. Does
NOT seed ``daily_candidates`` (Flow A creates those) or ``pending_review``
(Flow B creates those).

Two entry points:
- ``seed_if_empty(session)`` — no-op if problems already exist; used on
  terminal startup so a real DB with real data is never clobbered.
- ``reset_and_seed(session)`` — wipes the transient tables in FK-safe order
  then re-seeds; wired to ``:reset`` in the REPL.
"""

from __future__ import annotations

from sqlmodel import Session, select

from leetcode_coach.db.models import (
    DailyCandidate,
    LeetCodeLog,
    LeetCodeProblem,
    PendingReview,
    TutorLesson,
)

# ~15 realistic problems across easy/medium/hard and varied tag families.
# Slugs match real LeetCode slugs so the URLs Flow A emits are believable.
_PROBLEMS: list[dict] = [
    {"slug": "two-sum", "title": "Two Sum", "difficulty": "easy", "tags": "array,hash-table"},
    {"slug": "valid-parentheses", "title": "Valid Parentheses", "difficulty": "easy", "tags": "stack,string"},
    {"slug": "merge-two-sorted-lists", "title": "Merge Two Sorted Lists", "difficulty": "easy", "tags": "linked-list,recursion"},
    {"slug": "best-time-to-buy-and-sell-stock", "title": "Best Time to Buy and Sell Stock", "difficulty": "easy", "tags": "array,dynamic-programming"},
    {"slug": "binary-search", "title": "Binary Search", "difficulty": "easy", "tags": "array,binary-search"},
    {"slug": "merge-intervals", "title": "Merge Intervals", "difficulty": "medium", "tags": "array,sorting"},
    {"slug": "longest-substring-without-repeating-characters", "title": "Longest Substring Without Repeating Characters", "difficulty": "medium", "tags": "hash-table,string,sliding-window"},
    {"slug": "longest-palindromic-substring", "title": "Longest Palindromic Substring", "difficulty": "medium", "tags": "string,dynamic-programming"},
    {"slug": "container-with-most-water", "title": "Container With Most Water", "difficulty": "medium", "tags": "array,two-pointers,greedy"},
    {"slug": "3sum", "title": "3Sum", "difficulty": "medium", "tags": "array,two-pointers,sorting"},
    {"slug": "binary-tree-level-order-traversal", "title": "Binary Tree Level Order Traversal", "difficulty": "medium", "tags": "tree,bfs,binary-tree"},
    {"slug": "coin-change", "title": "Coin Change", "difficulty": "medium", "tags": "array,dynamic-programming,breadth-first-search"},
    {"slug": "word-search", "title": "Word Search", "difficulty": "medium", "tags": "array,backtracking,matrix"},
    {"slug": "median-of-two-sorted-arrays", "title": "Median of Two Sorted Arrays", "difficulty": "hard", "tags": "array,binary-search,divide-and-conquer"},
    {"slug": "regular-expression-matching", "title": "Regular Expression Matching", "difficulty": "hard", "tags": "string,dynamic-programming,recursion"},
    {"slug": "merge-k-sorted-lists", "title": "Merge k Sorted Lists", "difficulty": "hard", "tags": "linked-list,heap,priority-queue,divide-and-conquer"},
    {"slug": "trapping-rain-water", "title": "Trapping Rain Water", "difficulty": "hard", "tags": "array,two-pointers,stack,dynamic-programming"},
]

# 3 active lessons at varied reinforcement counts so the double-gated
# graduation (times_reinforced >= 5) can be exercised: one at 4 (one away),
# one at 2 (early), one at 5+ would already be a graduation candidate.
_LESSONS: list[dict] = [
    {"title": "Sliding window for substring problems", "category": "two-pointers", "times_reinforced": 4},
    {"title": "Binary search on sorted input", "category": "binary-search", "times_reinforced": 2},
    {"title": "Heap for k-way merge", "category": "heap", "times_reinforced": 1},
]


def _url(slug: str) -> str:
    return f"https://leetcode.com/problems/{slug}/"


def _insert_problems(session: Session) -> None:
    for p in _PROBLEMS:
        session.add(
            LeetCodeProblem(
                slug=p["slug"],
                title=p["title"],
                url=_url(p["slug"]),
                difficulty=p["difficulty"],
                tags=p["tags"],
                solved=False,
            )
        )


def _insert_lessons(session: Session) -> None:
    for les in _LESSONS:
        session.add(
            TutorLesson(
                title=les["title"],
                category=les["category"],
                times_reinforced=les["times_reinforced"],
                active=True,
            )
        )


def seed_if_empty(session: Session) -> bool:
    """Insert the fixture iff ``leetcode_problems`` is empty.

    Returns True if seeding happened, False if it was a no-op. Idempotent —
    running twice never duplicates. Safe on a DB that already has real data
    (the no-op path).
    """
    count = session.exec(select(LeetCodeProblem)).all()
    if count:
        return False
    _insert_problems(session)
    _insert_lessons(session)
    session.commit()
    return True


def reset_and_seed(session: Session) -> None:
    """Wipe transient tables in FK-safe order, then re-seed the fixture.

    Wipe order respects FKs: daily_candidates and pending_review reference
    leetcode_problems.slug; leetcode_log references it too. tutor_lessons and
    bot_state are independent. Wipe children before parents.
    """
    for model in (DailyCandidate, PendingReview, LeetCodeLog, TutorLesson, LeetCodeProblem):
        rows = session.exec(select(model)).all()
        for r in rows:
            session.delete(r)
    session.commit()
    _insert_problems(session)
    _insert_lessons(session)
    session.commit()
