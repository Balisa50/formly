"""Thin wrapper around the Groq REST API (OpenAI-compatible)."""
from __future__ import annotations

import time
import requests

from .config import GROQ_API_KEY, GROQ_MODEL

API_URL = "https://api.groq.com/openai/v1/chat/completions"


def chat(
    system: str,
    user: str,
    model: str = GROQ_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Send a chat completion request and return the assistant message."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(3):
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            wait = 2 ** attempt
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    raise RuntimeError("Groq API rate limit exceeded after 3 retries")
