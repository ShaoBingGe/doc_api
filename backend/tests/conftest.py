"""Test bootstrap — warm up SQLAlchemy mapper resolution before any test
imports run, to dodge the known circular-import edge case between
app.models.__init__ and app.ocr_optimizer.models on cold start.

This mirrors how the FastAPI app boot path resolves the chain.
"""

import app.models  # noqa: F401  — registers all mappers
import app.ocr_optimizer  # noqa: F401  — registers OCR mappers
