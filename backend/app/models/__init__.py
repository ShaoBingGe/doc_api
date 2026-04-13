"""
ORM Models package.

导入顺序遵循依赖关系，确保 SQLAlchemy 关系映射正确解析。
"""

from .base import Base, TimestampMixin, UUIDMixin
from .document import Document, DocumentStatus, ProcessingResult
from .annotation import Annotation, AnnotationSource, FieldType
from .conversation import Conversation, ConversationStatus, Message, MessageRole  # stub
from .api_definition import ApiDefinition, ApiDefinitionStatus
from .api_key import ApiKey
from .usage_record import UsageRecord
from .prompt_version import PromptVersion

__all__ = [
    # base
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    # document
    "Document",
    "DocumentStatus",
    "ProcessingResult",
    # annotation
    "Annotation",
    "AnnotationSource",
    "FieldType",
    # conversation (stub — API 端点暂未实现)
    "Conversation",
    "ConversationStatus",
    "Message",
    "MessageRole",
    # api definition
    "ApiDefinition",
    "ApiDefinitionStatus",
    # api key
    "ApiKey",
    # usage
    "UsageRecord",
    # prompt
    "PromptVersion",
]
