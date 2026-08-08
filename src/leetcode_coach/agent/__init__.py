"""OpenAI Agents SDK orchestration for LeetCode Coach V2.

The package deliberately depends on domain protocols instead of SQLModel objects so
the Telegram adapter and domain layer can evolve independently.
"""

from .advisor import SolAdvice, SolAdvisor
from .contracts import CoachDomain, RunStateRepository
from .orchestrator import AgentRunOutcome, AgentSettings, TerraCoachRunner, create_terra_agent
from .state import PendingApproval, SerializedRunState, text_confirmation

__all__ = [
    "AgentRunOutcome",
    "AgentSettings",
    "CoachDomain",
    "PendingApproval",
    "RunStateRepository",
    "SerializedRunState",
    "SolAdvice",
    "SolAdvisor",
    "TerraCoachRunner",
    "create_terra_agent",
    "text_confirmation",
]
