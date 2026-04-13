"""
Pydantic Schemas package.
"""

from .common import BoundingBox, ErrorDetail, ErrorResponse, PaginatedResponse
from .document import (
    DocumentDetail,
    DocumentResponse,
    DocumentUploadResponse,
    FieldHighlight,
    HighlightsResponse,
    ProcessingResultResponse,
    RegionOcrRequest,
    RegionOcrResponse,
    ReprocessRequest,
)
from .annotation import (
    AnnotationListResponse,
    AnnotationResponse,
    BatchAnnotationRequest,
    CreateAnnotationRequest,
    UpdateAnnotationRequest,
)
from .api_definition import (
    ApiDefinitionResponse,
    ApiDocsResponse,
    ApiStatsResponse,
    CreateApiDefinitionRequest,
    UpdateApiDefinitionRequest,
    UpdateApiStatusRequest,
)
from .api_key import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    UpdateApiKeyRequest,
)
from .extract import ExtractErrorResponse, ExtractMetadata, ExtractResponse, ExtractJsonRequest

__all__ = [
    # common
    "BoundingBox", "ErrorDetail", "ErrorResponse", "PaginatedResponse",
    # document
    "DocumentDetail", "DocumentResponse", "DocumentUploadResponse",
    "FieldHighlight", "HighlightsResponse",
    "ProcessingResultResponse", "RegionOcrRequest", "RegionOcrResponse", "ReprocessRequest",
    # annotation
    "AnnotationListResponse", "AnnotationResponse", "BatchAnnotationRequest",
    "CreateAnnotationRequest", "UpdateAnnotationRequest",
    # api definition
    "ApiDefinitionResponse", "ApiDocsResponse", "ApiStatsResponse",
    "CreateApiDefinitionRequest", "UpdateApiDefinitionRequest", "UpdateApiStatusRequest",
    # api key
    "ApiKeyResponse", "CreateApiKeyRequest", "CreateApiKeyResponse", "UpdateApiKeyRequest",
    # extract
    "ExtractErrorResponse", "ExtractMetadata", "ExtractResponse", "ExtractJsonRequest",
]
