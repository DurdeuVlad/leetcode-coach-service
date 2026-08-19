"""Bounded Terra orchestration built on the official OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from agents import RunContextWrapper
from pydantic import Field

from leetcode_coach.clock import local_today

from .advisor import SolAdvice, SolAdvisor
from .contracts import CoachDomain
from .tool_models import (
    AttemptHistoryFilters,
    CoachingMemoryUpdate,
    LessonDelta,
    ProblemPoolFilters,
    ProposalSelectionInput,
    SolAdvisorRequest,
)

TERRA_MODEL = "gpt-5.6-terra"
MAX_TURNS = 16
MAX_READ_TOOL_CONCURRENCY = 3
READ_TOOL_TIMEOUT_SECONDS = 20
CANONICAL_LOOKUP_TIMEOUT_SECONDS = 70
WRITE_TOOL_TIMEOUT_SECONDS = 30
AGENT_RUN_TIMEOUT_SECONDS = 600

if TYPE_CHECKING:
    from agents.memory.session import Session

CACHEABLE_TERRA_CONTEXT = """You are LeetCode Coach: practical, encouraging, honest,
and focused on helping one learner become interview-ready.

Assess the learner's profile and submitted work. Choose practice that targets weak
patterns and builds on demonstrated skills. Review correctness, complexity, code
quality, and reusable problem-solving patterns. Extract or reinforce useful lessons,
then adapt future practice from what the learner demonstrates.

Use the available tools when facts should be read or durable actions recorded. Treat
the learner's explicit report of completed work and supplied exact LeetCode identity
as sufficient to record it; a queue or pre-populated problem pool is optional. You may
propose as many or as few useful problems as the situation calls for. Keep responses
plain, concise, and coaching-oriented. Use Sol as a read-only advisor when it would
materially improve difficult guidance, while keeping final judgment yourself.

Pass attempted_on as "today", "yesterday", or an ISO-8601 date only when the user
states it; never invent one. To fix a recorded verdict, call correct_attempt on
that attempt_id instead of record_problem_attempt, which would duplicate credit.
"""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Agent knobs supplied by V2 configuration at composition time."""

    terra_model: str = TERRA_MODEL
    sol_model: str = "gpt-5.6-sol"
    max_turns: int = MAX_TURNS
    max_read_tool_concurrency: int = MAX_READ_TOOL_CONCURRENCY
    prompt_cache_ttl: str = "30m"

    @classmethod
    def from_config(cls, config: Any) -> AgentSettings:
        """Adapt V2Settings without making the orchestration layer import it."""

        return cls(
            terra_model=config.terra_model,
            sol_model=config.sol_advisor_model,
            max_turns=config.agent_max_turns,
            prompt_cache_ttl=config.prompt_cache_ttl,
        )

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_turns > 32:
            raise ValueError("max_turns must be between 1 and 32.")
        if self.max_read_tool_concurrency < 1 or self.max_read_tool_concurrency > 3:
            raise ValueError("max_read_tool_concurrency must be between 1 and 3.")
        if self.prompt_cache_ttl != "30m":
            raise ValueError("V2 requires the explicit GPT-5.6 prompt-cache TTL of 30m.")


def _sdk() -> tuple[Any, Any, Any, Any, Any, Any]:
    """Import at use time so non-agent unit tests remain importable without the SDK."""

    try:
        from agents import (
            Agent,
            ModelSettings,
            RunConfig,
            Runner,
            ToolExecutionConfig,
            function_tool,
        )
    except ImportError as exc:  # pragma: no cover - environment configuration
        raise RuntimeError(
            "OpenAI Agents SDK is required for V2 agent runs. Install the 'openai-agents' package."
        ) from exc
    return Agent, ModelSettings, RunConfig, Runner, ToolExecutionConfig, function_tool


def _bounded(value: Any, *, max_items: int = 30, max_text: int = 4_000) -> Any:
    """Keep tool output bounded even if a domain implementation forgets to trim it."""

    if isinstance(value, str):
        return value[:max_text]
    if isinstance(value, list):
        return [
            _bounded(item, max_items=max_items, max_text=max_text) for item in value[:max_items]
        ]
    if isinstance(value, tuple):
        return [
            _bounded(item, max_items=max_items, max_text=max_text) for item in value[:max_items]
        ]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, max_items=max_items, max_text=max_text)
            for key, item in list(value.items())[:max_items]
        }
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:max_text]


@dataclass(slots=True)
class AgentMetrics:
    model: str = TERRA_MODEL
    turns: int = 0
    tool_calls: int = 0
    sol_escalations: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    escalation_reason: str | None = None

    def finish(self) -> None:
        self.finished_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return data


@dataclass(slots=True)
class AgentRuntimeContext:
    """Ephemeral dependencies for exactly one chat run.

    The SDK RunState stores only ``chat_id``. The web layer must replace this object
    after reload, keeping DB connections and locks out of serialized agent state.
    """

    chat_id: int
    domain: CoachDomain
    sol_advisor: SolAdvisor
    read_limiter: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_READ_TOOL_CONCURRENCY)
    )
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sol_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    sol_calls: int = 0
    write_started: bool = False
    operation_key: str | None = None
    receipts: list[dict[str, Any]] = field(default_factory=list)

    async def read(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        async with self.read_limiter:
            return _bounded(await operation())

    async def write(self, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        # Ordinary coaching writes execute immediately, serially, and remain subject
        # to deterministic validation at the domain boundary.
        async with self.write_lock:
            self.write_started = True
            return _bounded(await operation())

    def sol_allowed(self) -> bool:
        return self.sol_calls == 0


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    status: str
    text: str | None
    metrics: dict[str, Any]
    receipts: list[dict[str, Any]] = field(default_factory=list)


def _usage_metrics(result: Any, metrics: AgentMetrics) -> None:
    """Best-effort extraction that tolerates minor SDK usage-shape revisions."""

    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return
    metrics.input_tokens = getattr(usage, "input_tokens", None)
    metrics.output_tokens = getattr(usage, "output_tokens", None)
    metrics.cached_tokens = getattr(usage, "cached_tokens", None) or getattr(
        getattr(usage, "input_tokens_details", None), "cached_tokens", None
    )
    metrics.cache_write_tokens = getattr(
        getattr(usage, "input_tokens_details", None), "cache_write_tokens", None
    )
    reported_tool_calls = getattr(usage, "tool_calls", None)
    emitted_tool_calls = sum(
        1
        for item in (getattr(result, "new_items", None) or [])
        if getattr(getattr(item, "raw_item", item), "type", None)
        in {"function_call", "computer_call", "hosted_tool_call"}
    )
    metrics.tool_calls = max(int(reported_tool_calls or 0), emitted_tool_calls)


def create_terra_agent(settings: AgentSettings | None = None) -> Any:
    """Construct a single-agent Terra coach with narrowly scoped domain tools."""

    settings = settings or AgentSettings()
    Agent, ModelSettings, _RunConfig, _Runner, _ToolExecutionConfig, function_tool = _sdk()
    from agents import ModelRetrySettings

    async def read_profile(ctx: RunContextWrapper[AgentRuntimeContext]) -> dict[str, Any]:
        """Return active lessons and a bounded recent-performance summary."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_learning_profile(chat_id=ctx.context.chat_id)
        )

    async def read_pool(
        ctx: RunContextWrapper[AgentRuntimeContext],
        filters: ProblemPoolFilters,
        mode: Literal["eligible_unsolved", "solved", "ineligible", "all"] = "eligible_unsolved",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search canonical eligible problems; use filters and at most 20 results."""
        limit = max(1, min(limit, 20))
        return await ctx.context.read(
            lambda: ctx.context.domain.search_problem_catalog(
                chat_id=ctx.context.chat_id,
                mode=mode,
                filters=filters.model_dump(),
                limit=limit,
            )
        )

    async def read_problem(
        ctx: RunContextWrapper[AgentRuntimeContext], slug: str
    ) -> dict[str, Any] | None:
        """Look up one canonical problem by slug."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_problem(chat_id=ctx.context.chat_id, slug=slug)
        )

    async def read_memory(ctx: RunContextWrapper[AgentRuntimeContext]) -> dict[str, Any]:
        """Return durable goals, preferences, availability, curriculum, mastery, and notes."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_coaching_memory(chat_id=ctx.context.chat_id)
        )

    async def read_attempt_history(
        ctx: RunContextWrapper[AgentRuntimeContext],
        filters: AttemptHistoryFilters,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search durable attempt history, optionally by slug or outcome."""
        return await ctx.context.read(
            lambda: ctx.context.domain.search_attempt_history(
                chat_id=ctx.context.chat_id,
                filters=filters.model_dump(exclude_none=True),
                limit=min(max(limit, 1), 50),
            )
        )

    async def read_follow_ups(
        ctx: RunContextWrapper[AgentRuntimeContext],
        status: Literal["scheduled", "delivered", "cancelled", "all"] = "scheduled",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List durable scheduled coaching follow-ups."""
        return await ctx.context.read(
            lambda: ctx.context.domain.list_follow_ups(
                chat_id=ctx.context.chat_id, status=status, limit=min(max(limit, 1), 50)
            )
        )

    async def read_queue(ctx: RunContextWrapper[AgentRuntimeContext]) -> dict[str, Any]:
        """Return proposed and active queue items."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_open_queue(chat_id=ctx.context.chat_id)
        )

    async def read_progress(ctx: RunContextWrapper[AgentRuntimeContext]) -> dict[str, Any]:
        """Return attempts, streak, lesson, and credit summaries."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_progress(chat_id=ctx.context.chat_id)
        )

    async def proposal(
        ctx: RunContextWrapper[AgentRuntimeContext],
        selections: Annotated[list[ProposalSelectionInput], Field(min_length=1, max_length=20)],
    ) -> dict[str, Any]:
        """Validate slug/reasoning/hint selections and return a canonical proposal preview."""
        if ctx.context.operation_key is None:
            raise RuntimeError("practice publication requires a Telegram message operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.publish_practice_set(
                chat_id=ctx.context.chat_id,
                selections=[selection.payload() for selection in selections],
                operation_key=ctx.context.operation_key,
            )
        )

    async def sol_enabled(ctx: RunContextWrapper[AgentRuntimeContext], _agent: Any) -> bool:
        return ctx.context.sol_allowed()

    async def sol_advice(
        ctx: RunContextWrapper[AgentRuntimeContext], request: SolAdvisorRequest
    ) -> dict[str, Any]:
        """Ask Sol once for read-only guidance; verify it using normal domain tools."""
        if not ctx.context.sol_allowed():
            return {"error": "Sol escalation was already used in this run."}
        async with ctx.context.sol_lock:
            if ctx.context.sol_calls:
                return {"error": "Sol escalation was already used in this run."}
            ctx.context.sol_calls += 1
            ctx.context.metrics.sol_escalations += 1
            ctx.context.metrics.escalation_reason = request.uncertainty[:500]
            advice: SolAdvice = await ctx.context.sol_advisor.advise(
                objective=request.objective,
                evidence=request.evidence_payload(),
                constraints=request.constraints,
                uncertainty=request.uncertainty,
            )
        return advice.to_dict()

    async def commit_picks(
        ctx: RunContextWrapper[AgentRuntimeContext], batch_id: str, slugs: list[str]
    ) -> dict[str, Any]:
        """Commit explicitly chosen canonical proposal slugs immediately."""
        return await ctx.context.write(
            lambda: ctx.context.domain.commit_picks(
                chat_id=ctx.context.chat_id, batch_id=batch_id, slugs=slugs
            )
        )

    async def start_problem(
        ctx: RunContextWrapper[AgentRuntimeContext],
        problem_slug: Annotated[str, Field(min_length=1, max_length=200)],
        title: Annotated[str | None, Field(max_length=300)] = None,
        difficulty: Literal["easy", "medium", "hard"] | None = None,
        tags: Annotated[str, Field(max_length=1000)] = "",
    ) -> dict[str, Any]:
        """Start or resume one exact LeetCode problem, supplying metadata if it is new."""
        return await ctx.context.write(
            lambda: ctx.context.domain.start_problem(
                chat_id=ctx.context.chat_id,
                problem_slug=problem_slug,
                title=title,
                difficulty=difficulty,
                tags=tags,
            )
        )

    async def update_memory(
        ctx: RunContextWrapper[AgentRuntimeContext], updates: CoachingMemoryUpdate
    ) -> dict[str, Any]:
        """Merge durable coaching memory using only supported coaching keys."""
        return await ctx.context.write(
            lambda: ctx.context.domain.update_coaching_memory(
                chat_id=ctx.context.chat_id, updates=updates.payload()
            )
        )

    async def commit_attempt(
        ctx: RunContextWrapper[AgentRuntimeContext],
        review_id: str,
        outcome: Literal["solved", "reviewed", "saw_solution", "attempted", "skipped"],
        feedback: str = "",
        lesson_delta: LessonDelta | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> dict[str, Any]:
        """Persist an explicitly confirmed attempt outcome immediately.

        outcome must be "solved" (code passed LeetCode) or "reviewed" (code was
        reviewed but did not pass — e.g. buggy, incomplete, or needs revision).
        Never use "needs_revision" or other values. Judge correctness only against
        the problem's own stated constraints: an issue in an input outside that
        domain (e.g. a value the constraints rule out) is not a defect and must not
        downgrade "solved" to "reviewed".
        """
        if ctx.context.operation_key is None:
            raise RuntimeError("attempt requires a Telegram message operation key")
        result = await ctx.context.write(
            lambda: ctx.context.domain.commit_attempt(
                chat_id=ctx.context.chat_id,
                review_id=review_id,
                outcome=outcome,
                feedback=feedback[:2_000],
                lesson_delta=lesson_delta.payload() if lesson_delta else None,
                operation_key=ctx.context.operation_key,
                language=language,
                solution_summary=solution_summary[:4_000],
                time_spent_min=time_spent_min,
            )
        )
        receipt = result.get("receipt")
        if isinstance(receipt, dict):
            ctx.context.receipts.append(receipt)
        return {key: value for key, value in result.items() if key != "receipt"}

    async def record_problem_attempt(
        ctx: RunContextWrapper[AgentRuntimeContext],
        problem_slug: Annotated[str, Field(min_length=1, max_length=200)],
        outcome: Literal["solved", "reviewed", "saw_solution", "attempted", "skipped"],
        title: Annotated[str | None, Field(max_length=300)] = None,
        difficulty: Literal["easy", "medium", "hard"] | None = None,
        tags: Annotated[str, Field(max_length=1_000)] = "",
        feedback: str = "",
        lesson_delta: LessonDelta | None = None,
        attempted_on: str | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> dict[str, Any]:
        """Record work using an exact LeetCode slug/URL and supplied identity metadata.

        attempted_on accepts today, yesterday, or an ISO-8601 calendar date. Judge
        outcome only against the problem's own stated constraints; an issue in an
        input outside that domain is not a defect and must not downgrade "solved"
        to "reviewed".
        """
        if ctx.context.operation_key is None:
            raise RuntimeError("problem attempt requires a Telegram message operation key")
        result = await ctx.context.write(
            lambda: ctx.context.domain.record_problem_attempt(
                chat_id=ctx.context.chat_id,
                problem_slug=problem_slug,
                title=title,
                difficulty=difficulty,
                tags=tags,
                outcome=outcome,
                feedback=feedback[:2_000],
                lesson_delta=lesson_delta.payload() if lesson_delta else None,
                operation_key=ctx.context.operation_key,
                attempted_on=attempted_on,
                language=language,
                solution_summary=solution_summary[:4_000],
                time_spent_min=time_spent_min,
            )
        )
        receipt = result.get("receipt")
        if isinstance(receipt, dict):
            ctx.context.receipts.append(receipt)
        return {key: value for key, value in result.items() if key != "receipt"}

    async def correct_attempt(
        ctx: RunContextWrapper[AgentRuntimeContext],
        attempt_id: str,
        reason: str,
        outcome: Literal["solved", "reviewed", "saw_solution", "attempted", "skipped"]
        | None = None,
        attempted_on: str | None = None,
        feedback: str | None = None,
        language: str | None = None,
        clear_language: bool = False,
        solution_summary: str | None = None,
        time_spent_min: int | None = None,
        clear_time_spent: bool = False,
    ) -> dict[str, Any]:
        """Correct an attempt with an append-only revision and compensating credit."""
        if ctx.context.operation_key is None:
            raise RuntimeError("attempt correction requires an operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.correct_attempt(
                chat_id=ctx.context.chat_id,
                attempt_id=attempt_id,
                outcome=outcome,
                attempted_on=attempted_on,
                feedback=feedback,
                language=language,
                clear_language=clear_language,
                solution_summary=solution_summary,
                time_spent_min=time_spent_min,
                clear_time_spent=clear_time_spent,
                reason=reason,
                operation_key=ctx.context.operation_key,
            )
        )

    async def reverse_attempt(
        ctx: RunContextWrapper[AgentRuntimeContext], attempt_id: str, reason: str
    ) -> dict[str, Any]:
        """Reverse a mistaken attempt with audit history and compensating credit."""
        if ctx.context.operation_key is None:
            raise RuntimeError("attempt reversal requires an operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.reverse_attempt(
                chat_id=ctx.context.chat_id,
                attempt_id=attempt_id,
                reason=reason,
                operation_key=ctx.context.operation_key,
            )
        )

    async def schedule_follow_up(
        ctx: RunContextWrapper[AgentRuntimeContext], due_at: str, message: str
    ) -> dict[str, Any]:
        """Schedule a coaching message from a Bucharest local wall time."""
        if ctx.context.operation_key is None:
            raise RuntimeError("follow-up requires an operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.schedule_follow_up(
                chat_id=ctx.context.chat_id,
                due_at=due_at,
                message=message,
                operation_key=ctx.context.operation_key,
            )
        )

    async def cancel_follow_up(
        ctx: RunContextWrapper[AgentRuntimeContext], follow_up_id: str
    ) -> dict[str, Any]:
        """Cancel one scheduled follow-up."""
        if ctx.context.operation_key is None:
            raise RuntimeError("follow-up cancellation requires an operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.cancel_follow_up(
                chat_id=ctx.context.chat_id,
                follow_up_id=follow_up_id,
                operation_key=ctx.context.operation_key,
            )
        )

    async def skip_problem(
        ctx: RunContextWrapper[AgentRuntimeContext], review_id: str
    ) -> dict[str, Any]:
        """Skip one active review immediately when the user requests it."""
        return await ctx.context.write(
            lambda: ctx.context.domain.skip_problem(
                chat_id=ctx.context.chat_id, review_id=review_id
            )
        )

    async def mark_solution_viewed(
        ctx: RunContextWrapper[AgentRuntimeContext], review_id: str
    ) -> dict[str, Any]:
        """Record that the user viewed a solution immediately."""
        return await ctx.context.write(
            lambda: ctx.context.domain.mark_solution_viewed(
                chat_id=ctx.context.chat_id, review_id=review_id
            )
        )

    async def reattempt_problem(
        ctx: RunContextWrapper[AgentRuntimeContext], review_id: str
    ) -> dict[str, Any]:
        """Create a reattempt from one review immediately."""
        return await ctx.context.write(
            lambda: ctx.context.domain.reattempt_problem(
                chat_id=ctx.context.chat_id, review_id=review_id
            )
        )

    async def extend_proposal(
        ctx: RunContextWrapper[AgentRuntimeContext], batch_id: str
    ) -> dict[str, Any]:
        """Extend one proposal batch immediately when the user requests it."""
        if ctx.context.operation_key is None:
            raise RuntimeError("proposal extension requires a Telegram message operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.extend_proposal(
                chat_id=ctx.context.chat_id,
                batch_id=batch_id,
                operation_key=ctx.context.operation_key,
            )
        )

    async def accept_credit_deficit(
        ctx: RunContextWrapper[AgentRuntimeContext], date: str
    ) -> dict[str, Any]:
        """Accept one daily credit deficit immediately."""
        return await ctx.context.write(
            lambda: ctx.context.domain.accept_credit_deficit(chat_id=ctx.context.chat_id, date=date)
        )

    async def adjust_lesson(
        ctx: RunContextWrapper[AgentRuntimeContext], lesson_delta: LessonDelta
    ) -> dict[str, Any]:
        """Apply an explicit lesson adjustment immediately."""
        if ctx.context.operation_key is None:
            raise RuntimeError("lesson adjustment requires a Telegram message operation key")
        return await ctx.context.write(
            lambda: ctx.context.domain.adjust_lesson(
                chat_id=ctx.context.chat_id,
                lesson_delta=lesson_delta.payload(),
                operation_key=ctx.context.operation_key,
            )
        )

    read_tools = [
        function_tool(
            read_profile, name_override="get_learning_profile", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_pool, name_override="search_problem_catalog", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_problem,
            name_override="get_problem",
            timeout=CANONICAL_LOOKUP_TIMEOUT_SECONDS,
        ),
        function_tool(
            read_memory, name_override="get_coaching_memory", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_attempt_history,
            name_override="search_attempt_history",
            timeout=READ_TOOL_TIMEOUT_SECONDS,
        ),
        function_tool(
            read_follow_ups, name_override="list_follow_ups", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_queue, name_override="get_open_queue", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_progress, name_override="get_progress", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            sol_advice,
            name_override="ask_sol_advisor",
            is_enabled=sol_enabled,
            timeout=WRITE_TOOL_TIMEOUT_SECONDS,
        ),
    ]
    write_tools = [
        function_tool(
            proposal, name_override="publish_practice_set", timeout=WRITE_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(start_problem, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(
            update_memory,
            name_override="update_coaching_memory",
            timeout=WRITE_TOOL_TIMEOUT_SECONDS,
        ),
        function_tool(commit_picks, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(commit_attempt, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(record_problem_attempt, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(correct_attempt, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(reverse_attempt, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(schedule_follow_up, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(cancel_follow_up, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(skip_problem, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(mark_solution_viewed, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(reattempt_problem, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(extend_proposal, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(accept_credit_deficit, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(adjust_lesson, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
    ]
    return Agent(
        name="LeetCode Coach",
        model=settings.terra_model,
        instructions=CACHEABLE_TERRA_CONTEXT,
        tools=[*read_tools, *write_tools],
        model_settings=ModelSettings(
            reasoning={"effort": "medium"},
            parallel_tool_calls=True,
            truncation="auto",
            prompt_cache_options={"mode": "explicit", "ttl": settings.prompt_cache_ttl},
            retry=ModelRetrySettings(max_retries=2),
        ),
    )


class TerraCoachRunner:
    """Run one fresh Terra interaction using durable conversation history."""

    def __init__(self, *, settings: AgentSettings | None = None) -> None:
        self._settings = settings or AgentSettings()

    def run_config(self) -> Any:
        _Agent, _ModelSettings, RunConfig, _Runner, ToolExecutionConfig, _function_tool = _sdk()
        return RunConfig(
            tool_execution=ToolExecutionConfig(
                max_function_tool_concurrency=self._settings.max_read_tool_concurrency
            )
        )

    @staticmethod
    def _input(message: str) -> list[dict[str, Any]]:
        # The breakpoint ends the stable prefix. Volatile user text follows it.
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": CACHEABLE_TERRA_CONTEXT,
                        "prompt_cache_breakpoint": {"mode": "explicit"},
                    },
                    {
                        "type": "input_text",
                        "text": (
                            f"Today in Europe/Bucharest: {local_today().isoformat()}\n\n"
                            f"{message[:8_000]}"
                        ),
                    },
                ],
            }
        ]

    async def run(
        self, *, message: str, context: AgentRuntimeContext, session: Session | None = None
    ) -> AgentRunOutcome:
        agent = create_terra_agent(self._settings)
        _Agent, _ModelSettings, _RunConfig, Runner, _ToolExecutionConfig, _function_tool = _sdk()
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                self._input(message),
                context=context,
                max_turns=self._settings.max_turns,
                run_config=self.run_config(),
                session=session,
            ),
            timeout=AGENT_RUN_TIMEOUT_SECONDS,
        )
        return await self._outcome(result=result, context=context)

    async def _outcome(self, *, result: Any, context: AgentRuntimeContext) -> AgentRunOutcome:
        _usage_metrics(result, context.metrics)
        context.metrics.turns = max(
            context.metrics.turns, len(getattr(result, "raw_responses", []) or [])
        )
        context.metrics.finish()
        final = getattr(result, "final_output", None)
        return AgentRunOutcome(
            "completed",
            str(final) if final is not None else "",
            context.metrics.to_dict(),
            receipts=list(context.receipts),
        )
