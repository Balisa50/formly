"""Match form fields to profile data using LLM-based semantic understanding."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime

from .form_reader import FormField
from .groq_client import chat
from . import db

MATCH_SYSTEM = """You are an intelligent form-filling assistant. Given a user's profile and a list of form fields, determine which profile value should fill each field.

You understand context, not just keywords:
- "Academic Background" maps to education history
- "Previous Employment" maps to work experience
- "Contact Information" maps to email/phone/address
- "Statement of Purpose" is an essay field
- "First Name" or "Name (First Name)" maps to first_name
- "Last Name" or "Name (Last Name)" maps to last_name
- "Subjects" maps to skills or areas of study
- "Hobbies" maps to hobbies/interests
- "State and City" or "State" maps to address components

CRITICAL RULES:
1. The "value" field must ALWAYS be a real human-readable value (a name, email, date, etc.)
2. NEVER put CSS selectors (#something, .something, input[...]) as a value
3. NEVER put technical identifiers as values
4. If you don't know the value, set value=null — do NOT guess with technical garbage
5. For selection/radio/autocomplete fields with options listed, the value MUST be one of the provided options. NEVER invent values that aren't in the options list.
6. Phone numbers should be just digits for "10 Digits" fields (no + prefix)
7. For checkbox fields, return a COMMA-SEPARATED list of option labels to check. Match the user's hobbies/interests/skills to the closest available checkbox options.
8. For "Hobbies" checkbox fields: look at the user's hobbies, interests, activities in their profile and select the matching options from the checkbox list.
9. For "Subjects" autocomplete fields: if options are listed, pick from those options. If the user has skills/education fields, match them to available subject options.
10. For cascading "State and City" fields: use the user's address to determine state and city.

Return ONLY valid JSON — an array of objects:
[
  {
    "selector": "the CSS selector of the field",
    "field_type": "the field type",
    "label": "the human-readable field label",
    "match_type": "direct|selection|essay|file|unknown",
    "profile_key": "the profile key that matches, or null",
    "value": "the REAL value to fill (a name, email, number, etc.), or null",
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

Be generous with matching — use the full profile context to find answers.
Try hard to match EVERY field. Only mark as "unknown" if you truly cannot infer it."""


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
    # Non-empty when the field lives inside an <iframe> (its frame URL).
    # Passed through from FormField so the filler routes to the right frame.
    frame_url: str = ""
    # Original options from the FormField (for select/radio/checkbox gap questions)
    options: list[str] = dc_field(default_factory=list)


# Country codes by nationality (lowercase key)
_COUNTRY_CODES = {
    "gambian": "220",
    "gambia": "220",
    "the gambia": "220",
    "senegalese": "221",
    "senegal": "221",
    "nigerian": "234",
    "nigeria": "234",
    "ghanaian": "233",
    "ghana": "233",
    "kenyan": "254",
    "kenya": "254",
    "south african": "27",
    "south africa": "27",
    "american": "1",
    "usa": "1",
    "british": "44",
    "uk": "44",
    "indian": "91",
    "india": "91",
}

# Common date formats to try when parsing profile dates
_DATE_FORMATS = [
    "%d/%m/%Y",   # 14/10/2002
    "%m/%d/%Y",   # 10/14/2002
    "%Y-%m-%d",   # 2002-10-14
    "%d-%m-%Y",   # 14-10-2002
    "%d %B %Y",   # 14 October 2002
    "%d %b %Y",   # 14 Oct 2002
    "%B %d, %Y",  # October 14, 2002
    "%b %d, %Y",  # Oct 14, 2002
]


def _fix_phone_for_digit_requirement(value: str, label: str, profile: dict) -> str:
    """If the label requires N digits, ensure the phone has exactly that many.

    When too short, zero-pad on the left instead of prepending country code.
    Fields that explicitly state a digit count (e.g. "10 Digits") expect a
    local number of that length, NOT an international format."""
    if not value or not isinstance(value, str):
        return value

    # Extract digit requirement from label (e.g. "10 Digits", "10 digits", "10-digit")
    digit_match = re.search(r"(\d{1,2})\s*(?:digits?|Digits?)", label)
    if not digit_match:
        return value

    required_digits = int(digit_match.group(1))
    digits_only = re.sub(r"\D", "", value)

    if len(digits_only) >= required_digits:
        # Too many digits — keep the rightmost N (strips country code prefix)
        return digits_only[-required_digits:]

    # Phone is shorter than required — zero-pad on the left
    return digits_only.zfill(required_digits)


def _normalize_date(value: str) -> str:
    """Convert a date string to YYYY-MM-DD for reliable JS/datepicker input."""
    if not value or not isinstance(value, str):
        return value

    # Already in ISO format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
        return value.strip()

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Couldn't parse — return original
    return value


def _is_phone_field(label: str, profile_key: str | None) -> bool:
    """Check if a field is a phone/mobile number field."""
    label_lower = (label or "").lower()
    key_lower = (profile_key or "").lower()
    phone_keywords = ("phone", "mobile", "tel", "cell", "contact number")
    return any(kw in label_lower or kw in key_lower for kw in phone_keywords)


def _is_date_field(label: str, profile_key: str | None, field_type: str) -> bool:
    """Check if a field is a date field."""
    if field_type == "date":
        return True
    label_lower = (label or "").lower()
    key_lower = (profile_key or "").lower()
    date_keywords = ("date", "dob", "birth", "birthday", "born")
    return any(kw in label_lower or kw in key_lower for kw in date_keywords)


def match_fields(fields: list[FormField], page_context: str = "") -> list[FieldMatch]:
    """Match form fields to profile data using LLM semantic understanding."""
    profile = db.get_full_profile()

    # Index the original fields by selector so we can recover frame_url after
    # the LLM returns its response (LLM only sees selector, not frame info).
    _field_by_selector: dict[str, FormField] = {f.selector: f for f in fields}

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
            # Pass the full options list so the LLM can pick the right one.
            # Hard-cap only to prevent pathological token counts (>200 options is unusual).
            desc["options"] = f.options[:200]
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
        value = m.get("value")
        field_type = m.get("field_type", "text")

        # SAFETY: Never allow CSS selectors or technical garbage as values
        if value and isinstance(value, str):
            if value.startswith("#") or value.startswith(".") or "input[" in value or "select-" in value or value.startswith("css-"):
                value = None

        label = m.get("label", "")
        profile_key = m.get("profile_key")

        # Smart phone number handling: pad with country code if digits required
        if value and _is_phone_field(label, profile_key):
            value = _fix_phone_for_digit_requirement(value, label, profile)

        # Smart date handling: normalize to YYYY-MM-DD for JS datepickers
        if value and _is_date_field(label, profile_key, field_type):
            value = _normalize_date(value)

        # Skip file upload fields — agent can't handle these
        if field_type == "file":
            matches.append(FieldMatch(
                selector=m["selector"],
                field_type=field_type,
                label=label,
                match_type="skipped",
                profile_key=None,
                value=None,
                confidence=0,
                note="File upload — must be done manually on the form",
            ))
            continue

        # Recover frame_url and options from the original FormField (not in LLM response)
        orig_field = _field_by_selector.get(m["selector"])
        frame_url = orig_field.frame_url if orig_field else ""
        options = list(orig_field.options) if orig_field and orig_field.options else []

        # ANTI-HALLUCINATION: for selection fields that have a real options list,
        # validate the LLM's chosen value actually exists in that list.
        # If it doesn't, null it out so the gap-filler or user prompt takes over.
        if value and options and field_type in ("select", "radio", "checkbox", "autocomplete", "native_select"):
            opts_lower = [o.lower().strip() for o in options]
            val_lower = value.lower().strip()
            matched_opt = next(
                (options[i] for i, ol in enumerate(opts_lower)
                 if val_lower == ol or val_lower in ol or ol in val_lower),
                None,
            )
            if matched_opt:
                value = matched_opt   # normalise to exact option text
            else:
                value = None          # hallucinated option — ask user instead

        matches.append(FieldMatch(
            selector=m["selector"],
            field_type=field_type,
            label=label,
            match_type=m.get("match_type", "unknown") if value is not None or m.get("needs_essay") else "unknown",
            profile_key=profile_key,
            value=value,
            confidence=float(m.get("confidence", 0)) if value else 0,
            needs_essay=bool(m.get("needs_essay", False)),
            note=m.get("note", ""),
            frame_url=frame_url,
            options=options,
        ))

    return matches


def get_unmatched(matches: list[FieldMatch]) -> list[FieldMatch]:
    """Return fields that need user input (unknown + low confidence)."""
    return [m for m in matches if m.match_type == "unknown" or (m.value is None and not m.needs_essay)]


def get_essay_fields(matches: list[FieldMatch]) -> list[FieldMatch]:
    """Return fields that need essay generation."""
    return [m for m in matches if m.needs_essay]
