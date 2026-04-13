"""
TaskRunner — 任务执行抽象层。

实现：
  SyncRunner — 在调用线程中直接同步执行处理函数（原型 / 测试友好）

扩展：添加 CeleryRunner 等，只需继承 TaskRunner 并实现 submit。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class TaskRunner(ABC):
    """Abstract interface for submitting background tasks."""

    @abstractmethod
    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Submit *fn* for execution with the given *args* / *kwargs*.

        Returns whatever *fn* returns (for sync runners) or a task handle
        (for async runners like Celery).
        """


class SyncRunner(TaskRunner):
    """
    Executes the callable synchronously in the current thread.

    Suitable for local development and unit tests; no broker required.
    """

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)
