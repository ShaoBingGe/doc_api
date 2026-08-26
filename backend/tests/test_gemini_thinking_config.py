"""Gemini 思考参数合并 —— runtime 与 env 的优先级。

修复的缺陷（code review）：让位判断用 `"thinking_config" not in merged`，
会把 env 继承的 thinking_config（GEMINI_THINKING_BUDGET）也当成"已被 level
占用"，导致调用方显式传的 runtime thinking_budget 被静默忽略。
"""

from __future__ import annotations

import pytest

pytest.importorskip("google.genai")

from app.processors.gemini_processor import GeminiProcessor  # noqa: E402

_apply = GeminiProcessor._apply_runtime_thinking


def test_runtime_budget_overrides_env_inherited_config():
    """回归主案：env 里已有 thinking_config 时，runtime budget 必须生效。"""
    from google.genai import types

    merged = {"thinking_config": types.ThinkingConfig(thinking_budget=999)}  # env 继承
    rc = {"thinking_budget": 128}
    _apply(merged, rc)
    assert merged["thinking_config"].thinking_budget == 128, "runtime 参数被 env 静默压掉了"
    assert "thinking_budget" not in rc, "已消费的 key 必须 pop 掉，不能漏进 merged"


def _level(tc) -> str:
    """SDK 会把 'low' 规范成 ThinkingLevel 枚举，取值统一小写后再比。"""
    lv = tc.thinking_level
    return str(getattr(lv, "value", lv)).lower()


def test_runtime_level_beats_runtime_budget():
    """同一次调用里两者都给时以 level 为准（3.x 不认 budget）。"""
    merged: dict = {}
    _apply(merged, {"thinking_level": "low", "thinking_budget": 128})
    tc = merged["thinking_config"]
    assert _level(tc) == "low"
    assert tc.thinking_budget is None


def test_level_alone_and_budget_alone():
    merged: dict = {}
    _apply(merged, {"thinking_level": "high"})
    assert _level(merged["thinking_config"]) == "high"

    merged2: dict = {}
    _apply(merged2, {"thinking_budget": 64})
    assert merged2["thinking_config"].thinking_budget == 64


def test_invalid_budget_leaves_env_config_untouched():
    """budget 非法（0/负数/非 int）时不动 merged 里既有的配置。"""
    from google.genai import types

    env_cfg = types.ThinkingConfig(thinking_budget=999)
    merged = {"thinking_config": env_cfg}
    _apply(merged, {"thinking_budget": 0})
    assert merged["thinking_config"] is env_cfg
