"""Generate conversational questions for missing profile data.

The key rule: every answer gets saved to the profile permanently.
The same question is never asked twice."""
from __future__ import annotations

import json

from .matcher import FieldMatch
from .groq_client import chat
from . import db

QUESTION_SYSTEM = """You are a warm, conversational assistant helping someone fill out a form. You need to ask them for information that is missing from their profile.

Rules:
- Ask ONE question at a time, naturally and specifically
- Reference the actual form and what it's asking for
- Never be robotic — be like a helpful friend
- If the field has options, list them so the user can pick
- Be brief — one or two sentences max

Examples of good questions:
- "This scholarship wants your National ID number — I don't have that yet. What is it?"
- "They're asking for your CGPA. I couldn't find it in your CV. Can you tell me?"
- "The form has a field for your gender with options: Male, Female, Other. Which one?"
- "They want to know your current country of residence. Where are you based?"

DO NOT ask for information you already have. Only ask for what's missing."""


def generate_question(field: FieldMatch, page_context: str = "") -> str:
    """Generate a natural conversational question for a missing field."""
    prompt = f"""The user is filling out a form.
Page context: {page_context}

This field needs an answer:
- Label: {field.label}
- Type: {field.field_type}
- Required: {field.selector}
{f'- Options: {", ".join(field.note.split(",")[:10])}' if field.field_type in ("select", "radio") else ""}

Generate a single conversational question to ask the user for this information."""

    return chat(system=QUESTION_SYSTEM, user=prompt, temperature=0.5, max_tokens=150)


def generate_questions_batch(fields: list[FieldMatch], page_context: str = "") -> list[tuple[FieldMatch, str]]:
    """Generate questions for multiple missing fields at once.

    Returns list of (field, question) tuples. Groups related fields
    to avoid overwhelming the user.
    """
    if not fields:
        return []

    # For efficiency, batch into one LLM call
    fields_desc = [
        {"label": f.label, "type": f.field_type, "required": "required" in f.selector}
        for f in fields
    ]

    prompt = f"""The user is filling out a form.
Page context: {page_context}

These fields are missing from their profile:
{json.dumps(fields_desc, indent=2)}

For EACH missing field, write a single conversational question. Return a JSON array of strings, one question per field. Be warm, specific, and brief."""

    response = chat(system=QUESTION_SYSTEM, user=prompt, temperature=0.5, max_tokens=1024)

    # Parse
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]

    try:
        questions = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: generate individually
        return [(f, generate_question(f, page_context)) for f in fields]

    # Pair questions with fields
    result = []
    for i, field in enumerate(fields):
        q = questions[i] if i < len(questions) else f"What should I put for '{field.label}'?"
        result.append((field, q))

    return result


def save_answer(field: FieldMatch, answer: str) -> None:
    """Save user's answer to the profile database permanently.

    Converts the field label into a normalized profile key so it can
    be matched again in future forms.
    """
    # Normalize the label into a key
    key = field.label.lower().strip()
    key = key.replace(" ", "_").replace("-", "_")
    # Remove common noise words
    for noise in ["please_", "enter_", "your_", "the_", "provide_"]:
        key = key.replace(noise, "")
    key = key.strip("_")

    if not key:
        key = f"custom_{field.selector}"

    db.set_profile(key, answer, "custom")
