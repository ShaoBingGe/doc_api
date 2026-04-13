"""
Hardcoded public API templates.

Each template defines a reusable extraction schema that users can subscribe to.
Subscribe = create a new ApiDefinition from the template.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.api_definition import CreateApiDefinitionRequest
from app.services import api_definition_service

# ── Template definitions ─────────────────────────────────────────────────────

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "cn-vat-invoice",
        "name": "中国增值税发票",
        "description": "提取中国增值税专用/普通发票的关键字段：发票号码、开票日期、购买方/销售方、金额、税额等",
        "country": "CN",
        "language": "zh",
        "mode": "invoice",
        "tags": ["发票", "增值税", "财务"],
        "processor_type": "gemini",
        "model_name": "gemini-2.5-flash",
        "response_schema": {
            "type": "object",
            "properties": {
                "invoice_code": {"type": "string", "description": "发票代码"},
                "invoice_number": {"type": "string", "description": "发票号码"},
                "invoice_date": {"type": "string", "description": "开票日期"},
                "buyer": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "tax_id": {"type": "string"},
                        "address": {"type": "string"},
                        "bank_account": {"type": "string"},
                    },
                },
                "seller": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "tax_id": {"type": "string"},
                        "address": {"type": "string"},
                        "bank_account": {"type": "string"},
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "amount": {"type": "number"},
                            "tax_rate": {"type": "string"},
                            "tax_amount": {"type": "number"},
                        },
                    },
                },
                "total_amount": {"type": "number", "description": "合计金额"},
                "total_tax": {"type": "number", "description": "合计税额"},
                "total_with_tax": {"type": "number", "description": "价税合计"},
            },
        },
        "sample_fields": ["invoice_code", "invoice_number", "invoice_date", "buyer", "seller", "items", "total_amount"],
    },
    {
        "id": "us-invoice",
        "name": "US Invoice",
        "description": "Extract fields from US-format invoices: invoice number, date, vendor, line items, totals, payment terms",
        "country": "US",
        "language": "en",
        "mode": "invoice",
        "tags": ["invoice", "billing", "finance"],
        "processor_type": "gemini",
        "model_name": "gemini-2.5-flash",
        "response_schema": {
            "type": "object",
            "properties": {
                "invoice_number": {"type": "string"},
                "invoice_date": {"type": "string"},
                "due_date": {"type": "string"},
                "vendor": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "address": {"type": "string"},
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                    },
                },
                "bill_to": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "address": {"type": "string"},
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "amount": {"type": "number"},
                        },
                    },
                },
                "subtotal": {"type": "number"},
                "tax": {"type": "number"},
                "total": {"type": "number"},
                "payment_terms": {"type": "string"},
                "currency": {"type": "string"},
            },
        },
        "sample_fields": ["invoice_number", "invoice_date", "vendor", "bill_to", "items", "total"],
    },
    {
        "id": "eu-invoice",
        "name": "European Invoice (VAT)",
        "description": "Extract EU VAT invoice fields: invoice number, VAT IDs, dates, line items, VAT breakdown, currency",
        "country": "EU",
        "language": "en",
        "mode": "invoice",
        "tags": ["invoice", "VAT", "Europe"],
        "processor_type": "gemini",
        "model_name": "gemini-2.5-flash",
        "response_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "invoice_date": {"type": "string"},
                "seller": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "vat_number": {"type": "string"},
                        "address": {"type": "string"},
                    },
                },
                "buyer": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "vat_number": {"type": "string"},
                        "address": {"type": "string"},
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "vat_rate": {"type": "number"},
                            "amount": {"type": "number"},
                        },
                    },
                },
                "total_excl_vat": {"type": "number"},
                "total_vat": {"type": "number"},
                "total_incl_vat": {"type": "number"},
                "currency": {"type": "string"},
            },
        },
        "sample_fields": ["invoice_id", "invoice_date", "seller", "buyer", "items", "total_incl_vat"],
    },
    {
        "id": "cn-id-card",
        "name": "中国身份证",
        "description": "提取中国居民身份证正反面信息：姓名、性别、民族、出生日期、地址、身份证号、签发机关、有效期",
        "country": "CN",
        "language": "zh",
        "mode": "identity",
        "tags": ["身份证", "证件", "OCR"],
        "processor_type": "gemini",
        "model_name": "gemini-2.5-flash",
        "response_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "姓名"},
                "gender": {"type": "string", "description": "性别"},
                "ethnicity": {"type": "string", "description": "民族"},
                "birth_date": {"type": "string", "description": "出生年月日"},
                "address": {"type": "string", "description": "住址"},
                "id_number": {"type": "string", "description": "身份证号码"},
                "issuing_authority": {"type": "string", "description": "签发机关"},
                "valid_from": {"type": "string", "description": "有效期起始"},
                "valid_until": {"type": "string", "description": "有效期截止"},
            },
        },
        "sample_fields": ["name", "gender", "birth_date", "address", "id_number"],
    },
    {
        "id": "receipt",
        "name": "Receipt / 购物小票",
        "description": "Extract receipt data: store name, date/time, items, subtotal, tax, total, payment method",
        "country": "GLOBAL",
        "language": "multi",
        "mode": "receipt",
        "tags": ["receipt", "retail", "expense"],
        "processor_type": "gemini",
        "model_name": "gemini-2.5-flash",
        "response_schema": {
            "type": "object",
            "properties": {
                "store_name": {"type": "string"},
                "store_address": {"type": "string"},
                "date": {"type": "string"},
                "time": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "number"},
                            "price": {"type": "number"},
                        },
                    },
                },
                "subtotal": {"type": "number"},
                "tax": {"type": "number"},
                "total": {"type": "number"},
                "payment_method": {"type": "string"},
                "currency": {"type": "string"},
            },
        },
        "sample_fields": ["store_name", "date", "items", "total", "payment_method"],
    },
    {
        "id": "passport",
        "name": "Passport / 护照",
        "description": "Extract passport MRZ and visual fields: name, nationality, passport number, DOB, expiry, issuing country",
        "country": "GLOBAL",
        "language": "multi",
        "mode": "identity",
        "tags": ["passport", "identity", "travel"],
        "processor_type": "gemini",
        "model_name": "gemini-2.5-flash",
        "response_schema": {
            "type": "object",
            "properties": {
                "surname": {"type": "string"},
                "given_names": {"type": "string"},
                "nationality": {"type": "string"},
                "passport_number": {"type": "string"},
                "date_of_birth": {"type": "string"},
                "sex": {"type": "string"},
                "place_of_birth": {"type": "string"},
                "date_of_issue": {"type": "string"},
                "date_of_expiry": {"type": "string"},
                "issuing_authority": {"type": "string"},
                "issuing_country": {"type": "string"},
                "mrz_line_1": {"type": "string"},
                "mrz_line_2": {"type": "string"},
            },
        },
        "sample_fields": ["surname", "given_names", "passport_number", "nationality", "date_of_birth", "date_of_expiry"],
    },
]

_TEMPLATES_BY_ID = {t["id"]: t for t in TEMPLATES}


# ── Public API ───────────────────────────────────────────────────────────────

def list_templates(
    country: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    result = TEMPLATES
    if country:
        result = [t for t in result if t["country"].upper() == country.upper()]
    if language:
        result = [t for t in result if t["language"].lower() == language.lower()]
    return result


def get_template(template_id: str) -> dict[str, Any] | None:
    return _TEMPLATES_BY_ID.get(template_id)


def subscribe_template(
    db: Session,
    template_id: str,
    custom_name: str | None = None,
) -> Any:
    """Create an ApiDefinition from a template. Returns ApiDefinitionResponse."""
    from app.core.exceptions import NotFoundError

    template = get_template(template_id)
    if not template:
        raise NotFoundError(f"Template '{template_id}' not found")

    short = uuid.uuid4().hex[:6]
    api_code = f"{template_id}-{short}"

    body = CreateApiDefinitionRequest(
        name=custom_name or template["name"],
        api_code=api_code,
        description=template["description"],
        response_schema=template["response_schema"],
        processor_type=template["processor_type"],
        model_name=template["model_name"],
        config={"source_template_id": template_id},
    )
    return api_definition_service.create_api_definition(db, body)
