"""Token loading posture: dev defaults warn, and fail-closed can be required."""
import pytest

from assetcore.service import auth


def test_explicit_tokens_env_is_used(monkeypatch):
    monkeypatch.setenv("ASSETCORE_TOKENS", '{"tok": "artist"}')
    assert auth.load_tokens() == {"tok": "artist"}


def test_default_dev_tokens_warn(monkeypatch, caplog):
    monkeypatch.delenv("ASSETCORE_TOKENS", raising=False)
    monkeypatch.delenv("ASSETCORE_REQUIRE_TOKENS", raising=False)
    with caplog.at_level("WARNING"):
        tokens = auth.load_tokens()
    assert tokens == auth.DEFAULT_TOKENS
    assert any("DEV tokens" in r.message for r in caplog.records)


def test_require_tokens_fails_closed_without_tokens(monkeypatch):
    monkeypatch.delenv("ASSETCORE_TOKENS", raising=False)
    monkeypatch.setenv("ASSETCORE_REQUIRE_TOKENS", "1")
    with pytest.raises(RuntimeError, match="ASSETCORE_REQUIRE_TOKENS"):
        auth.load_tokens()


def test_require_tokens_ok_when_tokens_present(monkeypatch):
    monkeypatch.setenv("ASSETCORE_REQUIRE_TOKENS", "1")
    monkeypatch.setenv("ASSETCORE_TOKENS", '{"tok": "artist"}')
    assert auth.load_tokens() == {"tok": "artist"}
