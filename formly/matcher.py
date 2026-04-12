"""Match form fields to profile data using LLM-based semantic understanding."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .form_reader import FormField
from .groq_client import chat
from . import db

MATCH_SYSTEM = """You are an intelligent form-filling assistant. Given a user's profile and a list of form fields, determine which profile value should fill each field.

You understand context, not just keywords:
- "Academic Background" maps to education history
- "Previous Employment" maps to work experience
- "Contact Information" maps to email/phone/address
- "Statement of Purpose" is an essay field

Return ONLY valid JSON — an array of objects:
[
  {
    "selector": "the CSS selector of the field",
    "field_type": "the field type",
    "label": "the field label",
    "match_type": "direct|selection|essay|file|unknown",
    "profile_key": "the profile key that matches, or null",
    "value": "the value to fill, or null if no match",
    "confidence": 0.0 to 1.0,
    "needs_essay": false,
    "note": "brief explanation of the match"
  }
]

Match types:
- "direct": straightforward profile value (name, email, phone, dates)
- "selection": dropdown/radio where you pick the best option from the list
- "essay": textarea needing a written response (personal statement, cover letter, etc.)
- "file": file upload field (usually CV/resume)
- "unknown": cannot match — user needs to provide this

For selection fields, pick the option that best matches the profile data.
For essay fields, set needs_essay=true and value=null.
For unknown fields, set value=null and confidence=0.

Be generous with matching — use the full profile context to find answers."""


@dataclass
class FieldMatch:
    selector: str
    field_type: str
    label: str
    match_type: str  # direct, selection, essay, file, unknown
    profile_key: str | None
    value: str | None
    confidence: float
    needs_essay: bool = False
    note: str = ""


def match_fields(fields: list[FormField], page_context: str = "") -> list[FieldMatch]:
    """Match form fields to profile data using LLM semantic understanding."""
    profile = db.get_full_profile()

    # Build the prompt
    fields_desc = []
    for f in fields:
        desc = {
            "selector": f.selector,
            "field_type": f.field_type,
            "label": f.label,
            "required": f.required,
        }
        if f.options:
            desc["options"] = f.options[:30]  # cap for token limits
        if f.max_length:
            desc["max_length"] = f.max_length
        if f.placeholder:
            desc["placeholder"] = f.placeholder
        fields_desc.append(desc)

    user_prompt = f"""Page context: {page_context}

User Profile:
{json.dumps(profile, indent=2, default=str)}

Form Fields:
{json.dumps(fields_desc, indent=2)}

Match each form field to the appropriate profile data. Return the JSON array."""

    response = chat(system=MATCH_SYSTEM, user=user_prompt, temperature=0.1)

    # Parse response
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]

    matches_raw = json.loads(cleaned)

    matches = []
    for m in matches_raw:
        matches.append(FieldMatch(
            selector=m["selector"],
            field_type=m.get("field_type", "text"),
            label=m.get("label", ""),
            match_type=m.get("match_type", "unknown"),
            profile_key=m.get("profile_key"),
            value=m.get("value"),
            confidence=float(m.get("confidence", 0)),
            needs_essay=bool(m.get("needs_essay", False)),
            note=m.get("note", ""),
        ))

    return matches


def get_unmatched(matches: list[FieldMatch]) -> list[FieldMatch]:
    """Return fields that need user input (unknown + low confidence)."""
    return [m for m in matches if m.match_type == "unknown" or (m.value is None and not m.needs_essay)]


def get_essay_fields(matches: list[FieldMatch]) -> list[FieldMatch]:
    """Return fields that need essay generation."""
    return [m for m in matches if m.needs_essay]
