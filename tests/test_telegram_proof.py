import pytest

from leetcode_coach.scripts.prove_telegram_transport import staging_settings_from_env


def test_staging_transport_proof_requires_explicit_send_authority(monkeypatch) -> None:
    monkeypatch.delenv("STAGING_TELEGRAM_ALLOW_SEND", raising=False)
    monkeypatch.setenv("STAGING_TELEGRAM_BOT_TOKEN", "staging-token")
    monkeypatch.setenv("STAGING_TELEGRAM_CHAT_ID", "123")

    with pytest.raises(RuntimeError, match="authorize staging sends"):
        staging_settings_from_env()
