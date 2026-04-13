"""
Gemini document processor.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.processors.base import DocumentProcessor, extract_json

logger = logging.getLogger(__name__)

try:
    from google.genai import types
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-genai package not available. Install with: pip install google-genai")


class GeminiLMModelParams(BaseModel):
    temperature: Optional[float] = Field(0.0, description="采样温度，0 表示确定性输出")
    seed: Optional[int] = Field(None, description="随机种子")
    top_p: Optional[float] = Field(None, description="Nucleus sampling 阈值")
    top_k: Optional[float] = Field(None, description="Top-K 采样阈值")
    candidate_count: Optional[int] = Field(None, description="返回候选响应数")
    max_output_tokens: Optional[int] = Field(None, description="最大输出 token 数")
    stop_sequences: Optional[List[str]] = Field(None, description="停止序列（最多5个）")
    presence_penalty: Optional[float] = Field(None, description="存在惩罚系数")
    frequency_penalty: Optional[float] = Field(None, description="频率惩罚系数")
    response_mime_type: Optional[str] = Field(
        "application/json",
        description="响应 MIME 类型：application/json 或 text/plain",
    )
    response_schema: Optional[Any] = Field(default=None, description="结构化输出 JSON Schema")
    response_modalities: Optional[List[str]] = Field(default=None, description="响应模态")
    thinking_config: Optional[Any] = Field(default=None, description="深度思考配置")


class GeminiProcessor(DocumentProcessor):
    """Gemini-based document processor."""

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        if not GEMINI_AVAILABLE:
            raise ImportError("google-genai package is required for GeminiProcessor")

        from app.core.config import get_settings
        settings = get_settings()

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            import os
            api_key = os.environ.get("API_KEY", "")

        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.llm_param_config = self._build_param_config({})

    def _build_param_config(self, custom_config: dict) -> dict:
        """Build LLM params from environment variables, merged with custom_config."""
        import os

        env_config: dict = {}
        _float = lambda k: float(os.environ[k]) if os.environ.get(k) else None
        _int = lambda k: int(os.environ[k]) if os.environ.get(k) else None

        for key, cast in [
            ("GEMINI_TEMPERATURE", _float),
            ("GEMINI_TOP_P", _float),
            ("GEMINI_PRESENCE_PENALTY", _float),
            ("GEMINI_FREQUENCY_PENALTY", _float),
            ("GEMINI_MAX_OUTPUT_TOKENS", _int),
            ("GEMINI_TOP_K", _int),
            ("GEMINI_SEED", _int),
            ("GEMINI_CANDIDATE_COUNT", _int),
        ]:
            val = cast(key)
            if val is not None:
                env_config[key.replace("GEMINI_", "").lower()] = val

        if os.environ.get("GEMINI_STOP_SEQUENCES"):
            env_config["stop_sequences"] = os.environ["GEMINI_STOP_SEQUENCES"].split(",")

        if os.environ.get("GEMINI_RESPONSE_MIME_TYPE"):
            env_config["response_mime_type"] = os.environ["GEMINI_RESPONSE_MIME_TYPE"]

        if os.environ.get("GEMINI_THINKING_BUDGET"):
            budget = int(os.environ["GEMINI_THINKING_BUDGET"])
            env_config["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)

        final = {**env_config, **custom_config}
        logger.debug("Built Gemini param config: %s", final)
        return final

    def _normalize_schema(self, schema: Any) -> Any:
        """Normalize JSON Schema: lowercase type names, strip misplaced required inside properties."""
        if not isinstance(schema, dict):
            return schema

        normalized: dict = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                normalized[key] = value.lower()
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

    def process_document(
        self,
        file_path: str,
        instruction: str,
        runtime_config: Optional[dict] = None,
    ) -> str:
        """
        Process a document file using Gemini and return a JSON string.

        Args:
            file_path: Path to the document (PDF / PNG / JPEG).
            instruction: System instruction for the model.
            runtime_config: Optional overrides (temperature, response_schema, etc.).

        Returns:
            JSON string with extracted data.
        """
        suffix = pathlib.Path(file_path).suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        mime_type = mime_map.get(suffix, "application/octet-stream")

        contents = [
            types.Part.from_bytes(
                data=pathlib.Path(file_path).read_bytes(),
                mime_type=mime_type,
            )
        ]

        merged = {**self.llm_param_config}
        if runtime_config:
            # Convert thinking_budget shorthand to ThinkingConfig object
            if "thinking_budget" in runtime_config:
                budget = runtime_config.pop("thinking_budget")
                if isinstance(budget, int) and budget > 0:
                    merged["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
                    logger.info("Converted thinking_budget %d to ThinkingConfig", budget)
            merged.update(runtime_config)

        if merged.get("response_schema"):
            merged["response_schema"] = self._normalize_schema(merged["response_schema"])

        # Set default thinking_config if not provided
        if "thinking_config" not in merged:
            merged["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

        param_config = GeminiLMModelParams(**merged).model_dump(exclude_none=True)
        param_config["system_instruction"] = [types.Part.from_text(text=instruction)]

        generate_config = types.GenerateContentConfig(**param_config)

        logger.info("Calling Gemini model: %s", self.model_name)
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=generate_config,
        )
        logger.info("Gemini response length: %d chars", len(response.text or ""))

        json_string = response.text or ""

        # Extract JSON from fenced block when using text/plain MIME type
        if merged.get("response_mime_type") == "text/plain":
            extracted = extract_json(json_string)
            if extracted:
                json_string = extracted[0]

        return json_string

    def get_model_version(self) -> str:
        return f"gemini|{self.model_name}"
