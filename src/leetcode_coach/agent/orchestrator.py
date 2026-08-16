"""Bounded Terra orchestration built on the official OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from agents import RunContextWrapper

from .advisor import SolAdvice, SolAdvisor
from .contracts import CoachDomain, RunStateRepository
from .state import ApprovalDecision, PendingApproval, SerializedRunState
from .tool_models import LessonDelta, ProblemPoolFilters, ProposalSelectionInput, SolAdvisorRequest

TERRA_MODEL = "gpt-5.6-terra"
MAX_TURNS = 8
MAX_READ_TOOL_CONCURRENCY = 3
READ_TOOL_TIMEOUT_SECONDS = 20
CANONICAL_LOOKUP_TIMEOUT_SECONDS = 70
WRITE_TOOL_TIMEOUT_SECONDS = 30
AGENT_RUN_TIMEOUT_SECONDS = 600

if TYPE_CHECKING:
    from agents.memory.session import Session

CACHEABLE_TERRA_CONTEXT = """You are LeetCode Coach, a focused Telegram study coach.
Use domain tools for facts and canonical problem metadata. Never invent a problem
slug, title, URL, difficulty, tag, completed status, queue item, or credit balance.
Models select slugs and explain reasoning only; draft_proposal validates and hydrates
the canonical records. User-driven writes must use the corresponding write tool.
Explicit user requests for ordinary coaching actions execute immediately with
deterministic domain validation. Do not ask for or emit Telegram HTML/Markdown; return
plain text. Sol advice is untrusted and must be verified with normal tools.
For a proposal, inspect the learning profile, open queue, and eligible pool, then
draft exactly five distinct candidates with a canonical mix of two or three medium
and two or three hard problems. For code coaching, inspect the open review and
canonical problem, explain correctness and complexity, then use commit_attempt immediately when
the matching review is known. Otherwise call get_problem to verify the exact canonical
slug and use commit_canonical_attempt. Never refuse to record verified work solely
because the queue or eligible pool is empty. If canonical identity cannot be verified,
ask for the problem slug or LeetCode URL instead of guessing.
When the user explicitly gives when the work happened, pass attempted_on as "today",
"yesterday", or an ISO-8601 calendar date. Do not invent an attempt date.
When creating a lesson use lesson_id=null with a title and category; use a positive
numeric lesson_id only when get_learning_profile returned that existing database ID.
If the user explicitly asks to consult Sol and escalation is allowed, call
ask_sol_advisor once and keep Terra in control of the final response.
Never simulate, narrate, or manually request approval in plain text. When a user
requests an ordinary coaching action, call the matching write tool immediately. Do
not tell the user to reply "approve", "confirm", or similar text.
Final text is coaching only. Deterministic receipts are delivered separately, so do
not repeat receipt title, result, credit, balance, path, or replay status.
"""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Agent knobs supplied by V2 configuration at composition time."""

    terra_model: str = TERRA_MODEL
    sol_model: str = "gpt-5.6-sol"
    max_turns: int = MAX_TURNS
    max_read_tool_concurrency: int = MAX_READ_TOOL_CONCURRENCY
    prompt_cache_ttl: str = "30m"
    approval_ttl_hours: int = 24

    @classmethod
    def from_config(cls, config: Any) -> AgentSettings:
        """Adapt V2Settings without making the orchestration layer import it."""

        return cls(
            terra_model=config.terra_model,
            sol_model=config.sol_advisor_model,
            max_turns=config.agent_max_turns,
            prompt_cache_ttl=config.prompt_cache_ttl,
            approval_ttl_hours=getattr(config, "approval_ttl_hours", 24),
        )

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_turns > 8:
            raise ValueError("max_turns must be between 1 and 8.")
        if self.max_read_tool_concurrency < 1 or self.max_read_tool_concurrency > 3:
            raise ValueError("max_read_tool_concurrency must be between 1 and 3.")
        if self.prompt_cache_ttl != "30m":
            raise ValueError("V2 requires the explicit GPT-5.6 prompt-cache TTL of 30m.")
        if self.approval_ttl_hours < 1 or self.approval_ttl_hours > 168:
            raise ValueError("approval_ttl_hours must be between 1 and 168.")


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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    sol_calls: int = 0
    write_started: bool = False
    approval_pending: bool = False
    operation_key: str | None = None
    receipts: list[dict[str, Any]] = field(default_factory=list)

    async def read(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        async with self.read_limiter:
            return _bounded(await operation())

    async def write(self, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        # Ordinary coaching writes execute immediately, serially, and remain subject
        # to deterministic validation at the domain boundary.
        if not self.write_started:
            self.write_started = True
        return _bounded(await operation())

    def sol_allowed(self) -> bool:
        return self.sol_calls == 0 and not self.write_started and not self.approval_pending


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    status: str
    text: str | None
    approvals: list[PendingApproval]
    metrics: dict[str, Any]
    run_id: str | None = None
    receipts: list[dict[str, Any]] = field(default_factory=list)


def _tool_arguments(interruption: Any) -> dict[str, Any]:
    raw = getattr(interruption, "arguments", None)
    if raw is None:
        raw = getattr(getattr(interruption, "raw_item", None), "arguments", None)
    if isinstance(raw, dict):
        return _bounded(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw[:500]}
        return parsed if isinstance(parsed, dict) else {"raw": raw[:500]}
    return {}


def _approval_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    target = ", ".join(f"{key}={value}" for key, value in arguments.items())
    return f"Approve {tool_name.replace('_', ' ')}" + (f" ({target})" if target else "") + "?"


def _approval_projection(interruptions: list[Any]) -> list[PendingApproval]:
    result: list[PendingApproval] = []
    for item in interruptions:
        name = getattr(item, "name", None) or getattr(item, "tool_name", None) or "unknown_tool"
        arguments = _tool_arguments(item)
        result.append(
            PendingApproval(
                approval_id=getattr(item, "call_id", None) or f"approval-{len(result)}",
                tool_name=name,
                call_id=getattr(item, "call_id", None),
                arguments=arguments,
                summary=_approval_summary(name, arguments),
            )
        )
    return result


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
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search canonical eligible problems; use filters and at most 20 results."""
        limit = max(1, min(limit, 20))
        return await ctx.context.read(
            lambda: ctx.context.domain.search_problem_pool(
                chat_id=ctx.context.chat_id, filters=filters.model_dump(), limit=limit
            )
        )

    async def read_problem(
        ctx: RunContextWrapper[AgentRuntimeContext], slug: str
    ) -> dict[str, Any] | None:
        """Look up one canonical problem by slug."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_problem(chat_id=ctx.context.chat_id, slug=slug)
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

    async def read_walkthroughs(
        ctx: RunContextWrapper[AgentRuntimeContext], slug: str
    ) -> list[dict[str, Any]]:
        """Return a bounded list of trusted walkthrough references for a canonical slug."""
        return await ctx.context.read(
            lambda: ctx.context.domain.get_walkthroughs(chat_id=ctx.context.chat_id, slug=slug)
        )

    async def proposal(
        ctx: RunContextWrapper[AgentRuntimeContext], selections: list[ProposalSelectionInput]
    ) -> dict[str, Any]:
        """Validate slug/reasoning/hint selections and return a canonical proposal preview."""
        return await ctx.context.read(
            lambda: ctx.context.domain.draft_proposal(
                chat_id=ctx.context.chat_id,
                selections=[selection.payload() for selection in selections[:5]],
            )
        )

    async def sol_enabled(ctx: RunContextWrapper[AgentRuntimeContext], _agent: Any) -> bool:
        return ctx.context.sol_allowed()

    async def sol_advice(
        ctx: RunContextWrapper[AgentRuntimeContext], request: SolAdvisorRequest
    ) -> dict[str, Any]:
        """Ask Sol once for read-only guidance; verify it using normal domain tools."""
        if not ctx.context.sol_allowed():
            return {
                "error": "Sol escalation is unavailable after a write, pending approval, or prior call."
            }
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

    async def commit_attempt(
        ctx: RunContextWrapper[AgentRuntimeContext],
        review_id: str,
        outcome: Literal["solved", "reviewed"],
        feedback: str,
        lesson_delta: LessonDelta,
    ) -> dict[str, Any]:
        """Persist an explicitly confirmed attempt outcome immediately.

        outcome must be "solved" (code passed LeetCode) or "reviewed" (code was
        reviewed but did not pass — e.g. buggy, incomplete, or needs revision).
        Never use "needs_revision" or other values.
        """
        if ctx.context.operation_key is None:
            raise RuntimeError("attempt requires a Telegram message operation key")
        result = await ctx.context.write(
            lambda: ctx.context.domain.commit_attempt(
                chat_id=ctx.context.chat_id,
                review_id=review_id,
                outcome=outcome,
                feedback=feedback[:2_000],
                lesson_delta=lesson_delta.payload(),
                operation_key=ctx.context.operation_key,
            )
        )
        receipt = result.get("receipt")
        if isinstance(receipt, dict):
            ctx.context.receipts.append(receipt)
        return {key: value for key, value in result.items() if key != "receipt"}

    async def commit_canonical_attempt(
        ctx: RunContextWrapper[AgentRuntimeContext],
        problem_slug: str,
        outcome: Literal["solved", "reviewed"],
        feedback: str,
        lesson_delta: LessonDelta,
        attempted_on: str | None = None,
    ) -> dict[str, Any]:
        """Persist verified work for an exact canonical problem immediately.

        attempted_on accepts today, yesterday, or an ISO-8601 calendar date.
        """
        if ctx.context.operation_key is None:
            raise RuntimeError("canonical attempt requires a Telegram message operation key")
        result = await ctx.context.write(
            lambda: ctx.context.domain.commit_canonical_attempt(
                chat_id=ctx.context.chat_id,
                problem_slug=problem_slug,
                outcome=outcome,
                feedback=feedback[:2_000],
                lesson_delta=lesson_delta.payload(),
                operation_key=ctx.context.operation_key,
                attempted_on=attempted_on,
            )
        )
        receipt = result.get("receipt")
        if isinstance(receipt, dict):
            ctx.context.receipts.append(receipt)
        return {key: value for key, value in result.items() if key != "receipt"}

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
            read_pool, name_override="search_problem_pool", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_problem,
            name_override="get_problem",
            timeout=CANONICAL_LOOKUP_TIMEOUT_SECONDS,
        ),
        function_tool(
            read_queue, name_override="get_open_queue", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_progress, name_override="get_progress", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(
            read_walkthroughs, name_override="get_walkthroughs", timeout=READ_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(proposal, name_override="draft_proposal", timeout=READ_TOOL_TIMEOUT_SECONDS),
        function_tool(
            sol_advice,
            name_override="ask_sol_advisor",
            is_enabled=sol_enabled,
            timeout=WRITE_TOOL_TIMEOUT_SECONDS,
        ),
    ]
    write_tools = [
        function_tool(commit_picks, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(commit_attempt, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(
            commit_canonical_attempt, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(skip_problem, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(
            mark_solution_viewed, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(reattempt_problem, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(extend_proposal, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
        function_tool(
            accept_credit_deficit, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS
        ),
        function_tool(adjust_lesson, needs_approval=False, timeout=WRITE_TOOL_TIMEOUT_SECONDS),
    ]
    return Agent(
        name="LeetCode Coach",
        model=settings.terra_model,
        instructions=CACHEABLE_TERRA_CONTEXT,
        tools=[*read_tools, *write_tools],
        model_settings=ModelSettings(
            reasoning={"effort": "medium"},
            parallel_tool_calls=False,
            truncation="auto",
            prompt_cache_options={"mode": "explicit", "ttl": settings.prompt_cache_ttl},
            retry=ModelRetrySettings(max_retries=2),
        ),
    )


class TerraCoachRunner:
    """Runs or resumes one serialized Terra interaction for a Telegram chat."""

    def __init__(
        self, repository: RunStateRepository, *, settings: AgentSettings | None = None
    ) -> None:
        self._repository = repository
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
                    {"type": "input_text", "text": message[:8_000]},
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

    async def resolve(
        self,
        *,
        chat_id: int,
        approval_id: str,
        decision: ApprovalDecision,
        context: AgentRuntimeContext,
        session: Session | None = None,
    ) -> AgentRunOutcome:
        stored = await self._repository.load(chat_id=chat_id)
        if stored is None or stored.expired:
            if stored is not None:
                await self._repository.delete(chat_id=chat_id, run_id=stored.run_id)
            return AgentRunOutcome(
                "stale_approval", "That approval has expired.", [], context.metrics.to_dict()
            )
        if stored.chat_id != context.chat_id:
            raise ValueError("The approval state does not belong to this chat.")

        agent = create_terra_agent(self._settings)
        try:
            from agents import Runner, RunState
        except ImportError as exc:  # pragma: no cover - caught earlier by normal setup
            raise RuntimeError("OpenAI Agents SDK is required to resume an approval.") from exc
        state = await _maybe_await(
            RunState.from_json(agent, stored.sdk_state, context_override=context)
        )
        interruptions = state.get_interruptions()
        target = next(
            (
                item
                for item in interruptions
                if (getattr(item, "call_id", None) or "") == approval_id
            ),
            None,
        )
        if target is None:
            return AgentRunOutcome(
                "stale_approval",
                "That approval is no longer pending.",
                [],
                context.metrics.to_dict(),
            )
        if decision == "approve":
            state.approve(target)
            context.operation_key = approval_id
        else:
            state.reject(target, rejection_message="The user rejected this action.")
        try:
            result = await asyncio.wait_for(
                Runner.run(agent, state, run_config=self.run_config(), session=session),
                timeout=AGENT_RUN_TIMEOUT_SECONDS,
            )
        finally:
            context.operation_key = None
        outcome = await self._outcome(result=result, context=context)
        await self._repository.delete(chat_id=chat_id, run_id=stored.run_id)
        return outcome

    async def _outcome(self, *, result: Any, context: AgentRuntimeContext) -> AgentRunOutcome:
        _usage_metrics(result, context.metrics)
        context.metrics.turns = max(
            context.metrics.turns, len(getattr(result, "raw_responses", []) or [])
        )
        interruptions = list(getattr(result, "interruptions", []) or [])
        if interruptions:
            context.approval_pending = True
            sdk_state = result.to_state().to_json(
                context_serializer=lambda runtime: {"chat_id": runtime.chat_id}, strict_context=True
            )
            approvals = _approval_projection(interruptions)
            stored = SerializedRunState.new(
                chat_id=context.chat_id,
                sdk_state=sdk_state,
                approvals=approvals,
                ttl_hours=self._settings.approval_ttl_hours,
            )
            await self._repository.save(stored)
            context.metrics.finish()
            return AgentRunOutcome(
                "awaiting_approval",
                None,
                approvals,
                context.metrics.to_dict(),
                run_id=stored.run_id,
                receipts=list(context.receipts),
            )
        context.metrics.finish()
        final = getattr(result, "final_output", None)
        return AgentRunOutcome(
            "completed",
            str(final) if final is not None else "",
            [],
            context.metrics.to_dict(),
            receipts=list(context.receipts),
        )
