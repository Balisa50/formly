"""Smart gap-filling — the agent tries to answer fields itself before asking the user.

Priority order:
1. Auto-infer from existing profile (e.g., country from phone, gender from name)
2. Use common defaults (e.g., "N/A" for optional fields)
3. For selection fields, pick the best option from context
4. Only ask the user when it genuinely can't figure it out

Every answer gets saved to the profile permanently.
The same question is never asked twice."""
from __future__ import annotations

import json

from .matcher import FieldMatch
from .groq_client import chat
from . import db

AUTOFILL_SYSTEM = """You are a smart form-filling agent. Given a user's profile and a list of unmatched form fields, try to INFER the correct value for each field.

You are allowed to make reasonable inferences:
- Country/nationality from phone number prefix or address
- Gender from first name (if common name)
- Full name from first + last name
- Current date for "date of application" fields
- "N/A" or leave blank for truly optional fields you can't infer
- For selection fields (dropdown/radio), pick the best matching option

For each field, return:
{
  "selector": "the field selector",
  "can_autofill": true/false,
  "value": "the inferred value (or null if can't infer)",
  "confidence": 0.0 to 1.0,
  "reason": "why you chose this value or why you can't"
}

Be smart but accurate. Don't guess wildly — if you're <50% confident, set can_autofill=false.
Return a JSON array."""

QUESTION_SYSTEM = """You are a warm, helpful assistant filling out a form with someone. You need to ask them for specific information.

Rules:
- NEVER show CSS selectors, HTML IDs, or technical details to the user
- Ask about the actual information needed, not the field name
- Be specific about what format you need
- If there are options to choose from, list them clearly
- Be brief — one or two sentences max
- Reference the actual form purpose

Good examples:
- "This form asks for your subjects or courses — what subjects would you like to select?"
- "They need your date of birth. What's your date of birth?"
- "What's your current CGPA on a 4.0 scale?"
- "Which gender should I select? Options: Male, Female, Prefer not to say"

BAD examples (never do these):
- "What should I put in #react-select-3-input?"
- "Can you tell me what goes in the input[name='field_23']?"
- "What's the value for selector .css-1234?"
"""


def try_autofill(fields: list[FieldMatch], page_context: str = "") -> tuple[list[FieldMatch], list[FieldMatch]]:
    """Try to auto-fill unmatched fields using AI inference.

    Returns: (auto_filled, still_unknown)
    """
    if not fields:
        return [], []

    profile = db.get_full_profile()

    fields_desc = []
    for f in fields:
        desc = {
            "selector": f.selector,
            "label": f.label,
            "field_type": f.field_type,
        }
        # Always prefer the real options list over the LLM's note string
        if f.options:
            desc["options"] = f.options
        elif f.note:
            desc["context"] = f.note
        fields_desc.append(desc)

    prompt = f"""Page context: {page_context}

User Profile:
{json.dumps(profile, indent=2, default=str)}

Unmatched form fields:
{json.dumps(fields_desc, indent=2)}

Try to infer the correct value for each field from the profile context. Be smart."""

    try:
        response = chat(system=AUTOFILL_SYSTEM, user=prompt, temperature=0.1, max_tokens=2048)
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]
        results = json.loads(cleaned)
    except Exception:
        return [], fields  # Can't auto-fill, return all as unknown

    auto_filled = []
    still_unknown = []

    for i, field in enumerate(fields):
        result = results[i] if i < len(results) else {"can_autofill": False}
        if result.get("can_autofill") and result.get("value") and result.get("confidence", 0) >= 0.5:
            field.value = result["value"]
            field.confidence = result["confidence"]
            field.match_type = "inferred"
            field.note = result.get("reason", "AI-inferred")
            # Save to profile
            _save_to_profile(field.label, result["value"])
            auto_filled.append(field)
        else:
            still_unknown.append(field)

    return auto_filled, still_unknown


def generate_question(field: FieldMatch, page_context: str = "") -> str:
    """Generate a natural, human-readable question — NEVER shows selectors or technical details."""

    # Build a clean description of what we need
    label = field.label
    # If the label looks like a CSS selector or technical garbage, describe it generically
    if label.startswith("#") or label.startswith(".") or "select-" in label or "input[" in label:
        label = "a required field"

    options_text = ""
    choice_types = ("select", "radio", "checkbox", "native_select", "autocomplete")
    if field.options and field.field_type in choice_types:
        options_text = f"\nAvailable options: {', '.join(field.options)}"
    elif field.note and field.field_type in choice_types:
        options_text = f"\nAvailable options: {field.note}"

    prompt = f"""The user is filling out a form.
Page context: {page_context}

Field needing an answer:
- What it asks for: {label}
- Type of field: {field.field_type}
- Context: {field.context if hasattr(field, 'context') else ''}
{options_text}

Write a single friendly question asking the user for this information. NEVER mention CSS selectors, HTML, or technical details."""

    return chat(system=QUESTION_SYSTEM, user=prompt, temperature=0.5, max_tokens=150)


def generate_questions_batch(fields: list[FieldMatch], page_context: str = "") -> list[tuple[FieldMatch, str]]:
    """Generate human-readable questions for multiple missing fields.

    Returns list of (field, question) tuples.
    """
    if not fields:
        return []

    # Clean up field descriptions — never expose selectors
    fields_desc = []
    for f in fields:
        label = f.label
        if label.startswith("#") or label.startswith(".") or "select-" in label or "input[" in label:
            label = f"required {f.field_type} field"

        desc = {"what_it_asks": label, "type": f.field_type}
        choice_types = ("select", "radio", "checkbox", "native_select", "autocomplete")
        if f.options and f.field_type in choice_types:
            desc["options"] = f.options
        elif f.note and f.field_type in choice_types:
            desc["options"] = f.note
        fields_desc.append(desc)

    prompt = f"""The user is filling out a form.
Page context: {page_context}

These fields still need answers:
{json.dumps(fields_desc, indent=2)}

For EACH field, write a single friendly question. NEVER mention CSS selectors or technical terms. Return a JSON array of strings."""

    response = chat(system=QUESTION_SYSTEM, user=prompt, temperature=0.5, max_tokens=1024)

    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]

    try:
        questions = json.loads(cleaned)
    except json.JSONDecodeError:
        return [(f, generate_question(f, page_context)) for f in fields]

    result = []
    for i, field in enumerate(fields):
        q = questions[i] if i < len(questions) else generate_question(field, page_context)
        result.append((field, q))

    return result


def _save_to_profile(label: str, value: str) -> None:
    """Save a value to the profile using a clean key derived from the label."""
    import re
    key = label.lower().strip()
    key = re.sub(r'[^a-z0-9\s_]', '', key)
    key = key.replace(" ", "_")
    for noise in ["please_", "enter_", "your_", "the_", "provide_", "select_"]:
        key = key.replace(noise, "")
    key = key.strip("_")

    if not key or key.startswith("react") or key.startswith("css"):
        key = f"custom_field_{abs(hash(label)) % 10000}"

    db.set_profile(key, value, "custom")


def save_answer(field: FieldMatch, answer: str) -> None:
    """Save user's answer to the profile database permanently."""
    _save_to_profile(field.label, answer)
