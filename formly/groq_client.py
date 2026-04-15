"""Thin wrapper around the Groq REST API (OpenAI-compatible).

Uses a model fallback chain because llama-3.3-70b-versatile is flaky on
the free tier (returns truncated/corrupt output AND hits rate limits
quickly). Strategy: try the most-reliable model first, fall back to
alternatives on 429 / garbage responses.
"""
from __future__ import annotations

import re
import time

import requests

from .config import GROQ_API_KEY

API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Ordered by reliability on free tier:
#   8b-instant   — fastest, most reliable, rarely rate-limited
#   gemma2-9b-it — solid alternative, separate quota pool
#   70b-versatile — nicer prose when it works, but flakiest
MODELS = [
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "llama-3.3-70b-versatile",
]


def _looks_like_garbage(text: str) -> bool:
    """Detect truncated/corrupt responses (e.g. '8\\ufffd' from 70b)."""
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) < 20:
        return True
    bad = sum(
        1 for c in stripped
        if c == "\ufffd" or (ord(c) < 32 and c not in "\n\r\t")
    )
    return bad > len(stripped) * 0.1


def _retry_after_seconds(resp: requests.Response, err_msg: str) -> float:
    """Pull wait time from Retry-After header or 'try again in 1.234s' text."""
    hdr = resp.headers.get("Retry-After")
    if hdr:
        try:
            return max(0.0, float(hdr))
        except ValueError:
            pass
    m = re.search(r"try again in ([\d.]+)s", err_msg, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 0.0


def _call_once(
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
) -> tuple[str, str, int]:
    """Single Groq call. Returns (content, finish_reason, status_code).

    Raises RuntimeError on non-200 (with parsed error message attached).
    """
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
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", {}).get("message", resp.text[:300])
        except Exception:
            err_msg = resp.text[:300]
        if resp.status_code == 429:
            wait = _retry_after_seconds(resp, err_msg)
            raise RuntimeError(
                f"__RATE_LIMIT__:{wait:.3f}:{model}:{err_msg}"
            )
        raise RuntimeError(f"Groq API {resp.status_code} ({model}): {err_msg}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Groq ({model}) returned no choices: {str(data)[:300]}")
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    finish = choices[0].get("finish_reason", "")
    return content, finish, resp.status_code


def chat(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Send a chat completion request. Tries multiple models for reliability.

    The legacy `model` parameter is ignored — we always use the fallback
    chain because pinning to one model caused production outages.
    """
    last_error = ""
    last_garbage_preview = ""
    last_model = ""

    for model_name in MODELS:
        # Each model gets up to 2 attempts: one fresh, one after a short
        # rate-limit wait (capped at 5s). Longer waits → move to next model.
        for attempt in (1, 2):
            try:
                content, finish, _ = _call_once(
                    model_name, system, user, temperature, max_tokens
                )
            except RuntimeError as exc:
                msg = str(exc)
                last_model = model_name
                if msg.startswith("__RATE_LIMIT__:"):
                    parts = msg.split(":", 3)
                    wait = float(parts[1]) if len(parts) > 1 else 0
                    reason = parts[3] if len(parts) > 3 else msg
                    last_error = f"429 ({model_name}): {reason[:200]}"
                    if attempt == 1 and 0 < wait <= 5:
                        time.sleep(wait + 0.2)
                        continue
                    break  # too-long wait or already retried → next model
                last_error = msg[:300]
                break  # non-429 error → next model

            if not _looks_like_garbage(content):
                return content

            # Garbage response — remember and try next model (no retry)
            last_garbage_preview = content[:80]
            last_error = (
                f"{model_name} returned garbage "
                f"(finish={finish}, preview={content[:60]!r})"
            )
            last_model = model_name
            break

    # Exhausted — raise with everything we know
    if last_garbage_preview:
        raise RuntimeError(
            f"All Groq models failed. Last: {last_model} "
            f"garbage={last_garbage_preview!r} / error={last_error}"
        )
    raise RuntimeError(f"Groq API failed on all models. Last: {last_error}")
