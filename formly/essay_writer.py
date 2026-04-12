"""Generate tailored essays and personal statements using Groq LLaMA 3.

Every essay is specific to the opportunity — no copy-paste between applications."""
from __future__ import annotations

import json

from .groq_client import chat
from . import db

ESSAY_SYSTEM = """You are an expert application essay writer. You write like the applicant themselves — genuine, specific, and personal.

Rules:
- Write in FIRST PERSON as the applicant
- Reference specific details from their background (real companies, real achievements, real skills)
- Tailor every sentence to the specific opportunity and organisation
- Never use generic phrases like "I am passionate about" or "I believe I would be a great fit"
- Be concrete: use numbers, names, specific projects
- Match the tone to the application type: formal for scholarships, professional for jobs, warm for grants
- Stay within the character/word limit if specified
- Sound human, not AI-generated

Your output should ONLY be the essay text, nothing else. No explanations, no headers."""


def write_essay(
    prompt: str,
    page_context: str = "",
    max_length: int | None = None,
    save: bool = True,
) -> str:
    """Write a tailored essay for a specific form field.

    Args:
        prompt: The question or field label from the form
        page_context: Information about the opportunity (URL, title, etc.)
        max_length: Character limit if any
        save: Whether to save to the essays table

    Returns:
        The generated essay text
    """
    profile = db.get_full_profile()

    # Get past approved essays for style consistency
    past = db.get_past_essays(3)
    past_context = ""
    if past:
        past_context = "\n\nPrevious essays by this applicant (for style reference):\n"
        for e in past[:2]:
            past_context += f"Q: {e['prompt'][:100]}\nA: {e['response'][:300]}...\n\n"

    length_instruction = ""
    if max_length:
        # Estimate word count from char limit
        word_estimate = max_length // 5
        length_instruction = f"\nIMPORTANT: Keep the response under {max_length} characters (approximately {word_estimate} words)."

    user_prompt = f"""Opportunity context: {page_context}

Question/Prompt: {prompt}
{length_instruction}

Applicant's Profile:
{json.dumps(profile, indent=2, default=str)}
{past_context}

Write the essay now. Output ONLY the essay text."""

    essay = chat(
        system=ESSAY_SYSTEM,
        user=user_prompt,
        temperature=0.6,
        max_tokens=max_length // 3 if max_length else 2048,
    )

    # Enforce length limit
    if max_length and len(essay) > max_length:
        essay = essay[:max_length].rsplit(" ", 1)[0]

    if save:
        db.save_essay(prompt=prompt, response=essay, context=page_context)

    return essay
