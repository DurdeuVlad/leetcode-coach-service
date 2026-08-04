import sqlalchemy as sa

from leetcode_coach_v2.db.models import (
    V2AgentRun,
    V2Attempt,
    V2BotState,
    V2ConversationItem,
    V2CreditLedger,
    V2Lesson,
    V2PendingApproval,
    V2PendingReview,
    V2ProcessedUpdate,
    V2ProposalBatch,
)


def test_telegram_identifiers_compile_as_postgres_bigint() -> None:
    chat_models = [
        V2Attempt,
        V2Lesson,
        V2ProposalBatch,
        V2PendingReview,
        V2CreditLedger,
        V2BotState,
        V2ProcessedUpdate,
        V2ConversationItem,
        V2AgentRun,
        V2PendingApproval,
    ]
    for model in chat_models:
        assert isinstance(model.__table__.c.chat_id.type, sa.BigInteger), model.__name__
    assert isinstance(V2ProcessedUpdate.__table__.c.update_id.type, sa.BigInteger)
    assert isinstance(V2ProposalBatch.__table__.c.telegram_message_id.type, sa.BigInteger)
    assert isinstance(V2PendingReview.__table__.c.telegram_message_id.type, sa.BigInteger)
    assert isinstance(V2PendingApproval.__table__.c.approval_message_id.type, sa.BigInteger)
