"""
Mock document processor for testing — no external API calls.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from app.processors.base import DocumentProcessor

logger = logging.getLogger(__name__)

_DEFAULT_MOCK_DATA = [
    {
        "docType": "invoice",
        "nameOfInvoice": "御請求書",
        "invoiceNumber": "20393",
        "invoiceDate": "2025-05-02",
        "totalAmount": 23210935.00,
        "totalTaxAmount": 2110085.00,
        "currency": "JPY",
        "billToName": "TVS REGZA株式会社",
        "billFromName": "株式会社 ヒト・コミュニケーションズ",
        "lineItems": [
            {
                "description": "コールセンター業務委託料",
                "quantity": 1,
                "unitPrice": 21100850.00,
                "totalPrice": 21100850.00,
                "taxAmount": 2110085.00,
            }
        ],
    }
]


class MockProcessor(DocumentProcessor):
    """Mock processor for testing purposes — returns fixture data instantly."""

    def __init__(self, model_name: str = "mock-v1.0", **kwargs):
        self.model_name = model_name
        logger.info("MockProcessor initialized with model: %s", model_name)

    def process_document(
        self,
        file_path: str,
        instruction: str,
        runtime_config: Optional[dict] = None,
    ) -> str:
        logger.info("MockProcessor: returning fixture data for %s", file_path)
        # Simulate minimal processing delay
        time.sleep(0.1)
        return json.dumps(_DEFAULT_MOCK_DATA, ensure_ascii=False)

    def get_model_version(self) -> str:
        return f"mock|{self.model_name}"
