"""Environment-backed configuration; no secrets belong in source control."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Local development convenience only. Render environment variables take precedence.
load_dotenv(PROJECT_ROOT / ".env", override=False)

def get_setting(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value

def docs_enabled() -> bool:
    return (get_setting("ENABLE_API_DOCS", "false") or "false").lower() in {"1", "true", "yes"}
