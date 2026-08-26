"""日志配置 —— 保证 `app.*` 的 INFO 真的能输出。

背景缺陷：进程里原本没有任何日志配置，根 logger 默认 WARNING 且无 handler，
于是代码里每一条 `logger.info(...)` 在生产环境都被静默吞掉。排障时只看得见
报错，看不见"任务完成 / 用了哪个模型 / 文本层是否命中"这类关键上下文。

这组用例按"记录能否抵达 handler"来断言，而不只是检查 level 数值 ——
level 对了但没有 handler 同样什么都不输出。
"""

from __future__ import annotations

import logging

from app.main import _configure_logging


def _emitting(logger_name: str, level: int, caplog) -> bool:
    """该 logger 以此级别打一条，是否真被 handler 收到。"""
    caplog.clear()
    with caplog.at_level(logging.DEBUG):   # 让 caplog 自身不成为瓶颈
        logging.getLogger(logger_name).log(level, "probe-%s", logger_name)
    return any(r.getMessage().startswith("probe-") for r in caplog.records)


def test_app_namespace_emits_info(caplog):
    """回归主案：app.* 的 INFO 必须能出来。"""
    _configure_logging()
    assert logging.getLogger("app").isEnabledFor(logging.INFO)
    assert _emitting("app.api.v1.open_api", logging.INFO, caplog)


def test_root_has_a_handler():
    """光设 level 不够 —— 没有 handler 一样什么都不输出。"""
    _configure_logging()
    assert logging.getLogger().handlers, "根 logger 必须有 handler"


def test_third_party_info_stays_quiet():
    """第三方库留在 WARNING，否则 sqlalchemy / httpx 的 INFO 会把日志刷爆。"""
    _configure_logging()
    assert not logging.getLogger("sqlalchemy.engine").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_third_party_warning_still_emits(caplog):
    """压低第三方不能压成哑巴 —— WARNING 及以上仍要出来。"""
    _configure_logging()
    assert _emitting("httpx", logging.WARNING, caplog)


def test_level_is_configurable(monkeypatch):
    """LOG_LEVEL 可以关掉业务 INFO（排查噪声时用）。"""
    from app.core.config import get_settings
    from app import main as main_mod

    s = get_settings()
    monkeypatch.setattr(s, "LOG_LEVEL", "WARNING", raising=False)
    monkeypatch.setattr(main_mod, "settings", s, raising=False)
    _configure_logging()
    assert not logging.getLogger("app").isEnabledFor(logging.INFO)
    assert logging.getLogger("app").isEnabledFor(logging.WARNING)

    monkeypatch.setattr(s, "LOG_LEVEL", "INFO", raising=False)
    _configure_logging()
