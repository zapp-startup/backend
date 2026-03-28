"""
LLM API bridge for synthetic text generation.
Uses OpenAI API when LLM_API_KEY is set and --use-llm is enabled.
Falls back to templates on failure or missing key.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass


def _get_api_key() -> str | None:
    """Read LLM_API_KEY from environment."""
    return os.environ.get("LLM_API_KEY")


def _call_openai(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 200,
    model: str = "gpt-4o-mini",
) -> str | None:
    """
    Call OpenAI Chat Completions API. Returns generated text or None on failure.
    """
    api_key = _get_api_key()
    if not api_key or not api_key.strip():
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key.strip())
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.8,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception:
        return None


def is_llm_available() -> bool:
    """Return True if LLM_API_KEY is set and API is reachable."""
    return bool(_get_api_key())
