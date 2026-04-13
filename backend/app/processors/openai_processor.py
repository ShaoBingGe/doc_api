"""
OpenAI document processor with Structured Outputs support.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Dict, Optional

from app.processors.base import DocumentProcessor

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("openai package not available. Install with: pip install openai")


class OpenAIDocumentProcessor(DocumentProcessor):
    """OpenAI document processor with Structured Outputs support."""

    def __init__(self, model_name: str = "gpt-4o", **kwargs):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package is required for OpenAIDocumentProcessor")

        self.model_name = model_name

        from app.core.config import get_settings
        settings = get_settings()

        api_key = settings.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")

        self.client = OpenAI(api_key=api_key)
        logger.info("OpenAI processor initialized with model: %s", model_name)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _supports_temperature(self) -> bool:
        return "gpt-5" not in self.model_name.lower()

    def _encode_image(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _is_image_file(self, filepath: str) -> bool:
        return filepath.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif"))

    def _convert_gemini_schema_to_openai(self, schema: Dict) -> Dict:
        """Convert Gemini-style schema to OpenAI Structured Outputs format."""

        def convert(obj):
            if not isinstance(obj, dict):
                return obj

            converted = {}
            for key, value in obj.items():
                if key == "type":
                    converted[key] = value
                elif key == "properties":
                    converted[key] = {k: convert(v) for k, v in value.items()}
                elif key == "items":
                    converted[key] = convert(value)
                elif key == "required":
                    continue  # rebuilt below
                else:
                    converted[key] = value

            if converted.get("type") == "object" and "properties" in converted:
                converted["required"] = list(converted["properties"].keys())
                converted["additionalProperties"] = False

                # Make originally optional properties nullable
                original_required = obj.get("required", [])
                for prop in converted["properties"]:
                    if prop not in original_required:
                        prop_schema = converted["properties"][prop]
                        if isinstance(prop_schema, dict) and "type" in prop_schema:
                            converted["properties"][prop] = {
                                "anyOf": [prop_schema, {"type": "null"}]
                            }

            return converted

        return convert(schema)

    def _create_response_format(self, response_schema: Optional[Dict]) -> Optional[Dict]:
        if not response_schema:
            return None
        openai_schema = self._convert_gemini_schema_to_openai(response_schema)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "strict": True,
                "schema": openai_schema,
            },
        }

    def _prepare_message_content(self, filepath: str, prompt: str):
        """Return (content_list, use_instructions_api)."""
        if self._is_image_file(filepath):
            b64 = self._encode_image(filepath)
            content = [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
            ]
            return content, False

        # PDF / other documents: upload with user_data purpose
        with open(filepath, "rb") as f:
            uploaded = self.client.files.create(file=f, purpose="user_data")
        self._uploaded_file_id = uploaded.id
        return [{"type": "input_file", "file_id": uploaded.id}], True

    # ── public interface ─────────────────────────────────────────────────────

    def process_document(
        self,
        file_path: str,
        instruction: str,
        runtime_config: Optional[Dict] = None,
    ) -> str:
        """
        Process a document using the OpenAI Responses API.

        Args:
            file_path: Path to the document.
            instruction: Processing instruction.
            runtime_config: Optional overrides (temperature, max_output_tokens,
                            response_schema).

        Returns:
            Extracted content as a string (JSON when schema provided).
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Document not found: {file_path}")

        cfg = runtime_config or {}
        temperature = cfg.get("temperature", 0.1)
        max_tokens = cfg.get("max_output_tokens", 4096)
        response_schema = cfg.get("response_schema")

        logger.info("Processing document with OpenAI %s: %s", self.model_name, file_path)

        try:
            content, use_instructions = self._prepare_message_content(file_path, instruction)
            response_format = self._create_response_format(response_schema)

            if response_format:
                # Structured outputs require chat.completions
                chat_params: dict = {
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": max_tokens,
                    "response_format": response_format,
                }
                if self._supports_temperature():
                    chat_params["temperature"] = temperature

                resp = self.client.chat.completions.create(**chat_params)
                result = resp.choices[0].message.content
            else:
                api_params: dict = {
                    "model": self.model_name,
                    "input": [{"role": "user", "content": content}],
                    "max_output_tokens": max_tokens,
                }
                if use_instructions:
                    api_params["instructions"] = instruction
                if self._supports_temperature():
                    api_params["temperature"] = temperature

                resp = self.client.responses.create(**api_params)

                result = None
                if hasattr(resp, "output_text") and resp.output_text:
                    result = resp.output_text
                elif hasattr(resp, "output") and resp.output:
                    for item in resp.output:
                        for c in getattr(item, "content", []):
                            if hasattr(c, "text"):
                                result = c.text
                                break
                        if result:
                            break

            if not result:
                raise RuntimeError("No response content received from OpenAI")

            logger.info("OpenAI response length: %d chars", len(result))
            return result

        except Exception as exc:
            logger.error("OpenAI document processing failed: %s", exc)
            raise RuntimeError(f"OpenAI document processing failed: {exc}") from exc
        finally:
            if hasattr(self, "_uploaded_file_id"):
                try:
                    self.client.files.delete(self._uploaded_file_id)
                    logger.info("Deleted uploaded file: %s", self._uploaded_file_id)
                except Exception as e:
                    logger.warning("Failed to delete uploaded file: %s", e)
                finally:
                    del self._uploaded_file_id

    def get_model_version(self) -> str:
        return f"openai|{self.model_name}"
