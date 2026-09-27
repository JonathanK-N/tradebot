"""Règle absolue n°6 : aucun ordre sur compte réel sans décision explicite et tracée."""

import pytest

from tradebot.core.config import AppConfig
from tradebot.live.factory import REAL_MONEY_TOKEN, RealMoneyLocked, assert_real_money_allowed


def test_demo_always_allowed():
    assert_real_money_allowed(AppConfig(), is_demo=True)


def test_real_account_locked_by_default(monkeypatch):
    monkeypatch.delenv("TRADEBOT_ALLOW_REAL_MONEY", raising=False)
    with pytest.raises(RealMoneyLocked):
        assert_real_money_allowed(AppConfig(), is_demo=False)


def test_env_var_alone_is_not_enough(monkeypatch):
    monkeypatch.setenv("TRADEBOT_ALLOW_REAL_MONEY", REAL_MONEY_TOKEN)
    with pytest.raises(RealMoneyLocked):
        assert_real_money_allowed(AppConfig(environment="dev"), is_demo=False)


def test_both_conditions_unlock(monkeypatch):
    monkeypatch.setenv("TRADEBOT_ALLOW_REAL_MONEY", REAL_MONEY_TOKEN)
    assert_real_money_allowed(AppConfig(environment="live"), is_demo=False)
