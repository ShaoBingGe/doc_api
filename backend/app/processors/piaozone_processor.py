"""
PiaoZone document processor — routes requests through the PiaoZone AI gateway.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
from typing import Any, List, Optional

import requests
from pydantic import BaseModel, Field

from app.processors.base import DocumentProcessor, extract_json
from app.processors.piaozone_token import get_piaozone_token

logger = logging.getLogger(__name__)


class PiaoZoneModelParams(BaseModel):
    temperature: Optional[float] = Field(0.1, description="采样温度")
    seed: Optional[int] = Field(12345, description="随机种子")
    top_p: Optional[float] = Field(None, description="Nucleus sampling 阈值")
    top_k: Optional[float] = Field(None, description="Top-K 采样阈值")
    max_output_tokens: Optional[int] = Field(None, description="最大输出 token 数")
    response_mime_type: Optional[str] = Field("application/json", description="响应 MIME 类型")
    response_schema: Optional[Any] = Field(default=None, description="结构化输出 JSON Schema")
    thinking_budget: Optional[int] = Field(0, description="深度思考预算")


class PiaoZoneProcessor(DocumentProcessor):
    """PiaoZone AI gateway document processor."""

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.api_url = os.environ.get(
            "PIAOZONE_API_URL",
            "https://api-sit.piaozone.com/ai/knowledge/v1/chat/completions",
        )
        self.model_name = model_name
        self.llm_param_config = self._build_param_config({})
        logger.info("PiaoZone processor initialized with model: %s", model_name)

    def _build_param_config(self, custom_config: dict) -> dict:
        import os

        env_config: dict = {}
        _float = lambda k: float(os.environ[k]) if os.environ.get(k) else None
        _int = lambda k: int(os.environ[k]) if os.environ.get(k) else None

        for key, cast in [
            ("PIAOZONE_TEMPERATURE", _float),
            ("PIAOZONE_TOP_P", _float),
            ("PIAOZONE_MAX_OUTPUT_TOKENS", _int),
            ("PIAOZONE_TOP_K", _int),
            ("PIAOZONE_SEED", _int),
            ("PIAOZONE_THINKING_BUDGET", _int),
        ]:
            val = cast(key)
            if val is not None:
                env_config[key.replace("PIAOZONE_", "").lower()] = val

        if os.environ.get("PIAOZONE_RESPONSE_MIME_TYPE"):
            env_config["response_mime_type"] = os.environ["PIAOZONE_RESPONSE_MIME_TYPE"]

        return {**env_config, **custom_config}

    def _normalize_schema(self, schema: Any) -> Any:
        """Normalize JSON Schema: uppercase type names (PiaoZone API requirement)."""
        if not isinstance(schema, dict):
            return schema

        normalized: dict = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                normalized[key] = value.upper()
            elif key == "properties" and isinstance(value, dict):
                normalized[key] = {
                    k: self._normalize_schema(v)
                    for k, v in value.items()
                    if k != "required"
                }
            elif key in ("items", "anyOf", "oneOf", "allOf"):
                if isinstance(value, dict):
                    normalized[key] = self._normalize_schema(value)
                elif isinstance(value, list):
                    normalized[key] = [self._normalize_schema(i) for i in value]
            else:
                normalized[key] = value

        return normalized

    def _get_mime_type(self, file_path: str) -> str:
        suffix = pathlib.Path(file_path).suffix.lower()
        return {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(suffix, "application/octet-stream")

    def _build_request_body(
        self,
        file_path: str,
        instruction: str,
        param_config: dict,
    ) -> dict:
        """Build the API request body depending on file type."""
        mime_type = self._get_mime_type(file_path)
        file_name = os.path.basename(file_path)

        # Plain-text files: send content inline
        if mime_type == "application/octet-stream" and file_name.endswith(".txt"):
            try:
                text_content = pathlib.Path(file_path).read_text(encoding="utf-8")
                return {
                    "llm_type": "gemini",
                    "data": {
                        "model": self.model_name,
                        "contents": [
                            {
                                "parts": [
                                    {"text": f"Document content:\n{text_content}\n\nInstruction: {instruction}"}
                                ]
                            }
                        ],
                        "generation_config": param_config,
                    },
                }
            except Exception as exc:
                logger.warning("Could not read text file, falling back to file format: %s", exc)

        # PDF / images: base64-encode
        file_base64 = base64.b64encode(pathlib.Path(file_path).read_bytes()).decode()
        return {
            "llm_type": "gemini",
            "data": {
                "model": self.model_name,
                "contents": [
                    {
                        "type": "file",
                        "file": {"mime_type": mime_type, "data": file_base64, "name": file_name},
                    }
                ],
                "generation_config": {**param_config, "system_instruction": instruction},
            },
        }

    def process_document(
        self,
        file_path: str,
        instruction: str,
        runtime_config: Optional[dict] = None,
    ) -> str:
        """
        Process a document through the PiaoZone AI gateway.

        Args:
            file_path: Path to the document.
            instruction: Processing instruction.
            runtime_config: Optional overrides (temperature, response_schema, etc.).

        Returns:
            Extracted content as a JSON string.
        """
        merged = {**self.llm_param_config}
        if runtime_config:
            merged.update(runtime_config)

        if merged.get("response_schema"):
            merged["response_schema"] = self._normalize_schema(merged["response_schema"])

        param_config = PiaoZoneModelParams(**merged).model_dump(exclude_none=True)
        request_body = self._build_request_body(file_path, instruction, param_config)

        # --- logging (truncate base64 data) ---
        log_body = json.loads(json.dumps(request_body))
        for item in log_body.get("data", {}).get("contents", []):
            if "file" in item and "data" in item["file"]:
                item["file"]["data"] = f"<base64 len={len(item['file']['data'])}>"
        logger.info("PiaoZone request: %s", json.dumps(log_body, ensure_ascii=False))

        access_token = get_piaozone_token()
        url = f"{self.api_url}?access_token={access_token}"

        try:
            resp = requests.post(
                url,
                json=request_body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "client-platform": "common",
                },
                timeout=120,
            )
            if resp.status_code >= 400:
                logger.error("PiaoZone error response: %s", resp.text)
            resp.raise_for_status()

            data = resp.json()

            # Parse response: prefer parsed > candidates > choices fallbacks
            content = None
            if data.get("data", {}).get("parsed") is not None:
                content = json.dumps(data["data"]["parsed"], ensure_ascii=False)
            elif (
                data.get("data", {}).get("candidates")
                and data["data"]["candidates"][0]
                .get("content", {})
                .get("parts")
            ):
                content = data["data"]["candidates"][0]["content"]["parts"][0].get("text", "")
            elif data.get("choices"):
                content = data["choices"][0].get("message", {}).get("content", "")
            elif data.get("data", {}).get("content"):
                content = data["data"]["content"]

            if content is None:
                content = json.dumps(data, ensure_ascii=False)
                logger.warning("Could not extract content from PiaoZone response, returning full body")

            # Extract JSON from plain-text fenced blocks
            if isinstance(content, str) and merged.get("response_mime_type") == "text/plain":
                extracted = extract_json(content)
                content = extracted[0] if extracted else content

            logger.info("PiaoZone response (first 200): %s", content[:200])
            return content

        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"PiaoZone API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse PiaoZone response: {exc}") from exc

    def get_model_version(self) -> str:
        return f"piaozone|{self.model_name}"
