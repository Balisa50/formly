"""The Formly Agent — autonomous form filling with live progress.

Single entry point: give it a URL, it does everything.
Returns progress events as it works, asks for missing info, fills gaps."""
from __future__ import annotations

import asyncio
import base64
import json
import random
from dataclasses import dataclass, field
from typing import Generator

from . import db
from .form_reader import read_form
from .matcher import match_fields, FieldMatch
from .gap_filler import try_autofill, generate_question
from .essay_writer import write_essay
from .groq_client import chat


@dataclass
class AgentEvent:
    type: str  # "progress", "filled", "asking", "essay", "screenshot", "error", "done"
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return {"type": self.type, "message": self.message, "data": self.data}


def run_agent(url: str) -> list[AgentEvent]:
    """Run the full agent pipeline on a URL. Returns a list of events.

    Events flow:
    1. progress: "Reading form..."
    2. progress: "Found X fields"
    3. progress: "Matching to your profile..."
    4. filled: "Filled First Name: Abdoulie" (for each field)
    5. asking: "What's your X?" (for unknown fields)
    6. essay: "Writing response for X..." (for essay fields)
    7. screenshot: base64 image of filled form
    8. done: summary
    """
    events: list[AgentEvent] = []

    # Step 1: Read the form
    events.append(AgentEvent("progress", f"Opening {url} and reading all fields..."))

    try:
        fields, page_context = read_form(url)
    except Exception as e:
        events.append(AgentEvent("error", f"Could not open this form: {str(e)[:200]}"))
        return events

    events.append(AgentEvent("progress", f"Found {len(fields)} fields on the form."))

    # Step 2: Match to profile
    events.append(AgentEvent("progress", "Matching fields to your profile..."))

    try:
        matches = match_fields(fields, page_context)
    except Exception as e:
        events.append(AgentEvent("error", f"Matching failed: {str(e)[:200]}"))
        return events

    # Categorize
    auto_filled = [m for m in matches if m.value and m.confidence >= 0.5 and m.match_type != "skipped"]
    unknown = [m for m in matches if m.match_type == "unknown" and not m.needs_essay and m.field_type != "file"]
    essays = [m for m in matches if m.needs_essay]
    skipped = [m for m in matches if m.match_type == "skipped" or m.field_type == "file"]

    # Report auto-filled fields
    for m in auto_filled:
        label = _clean_label(m.label)
        events.append(AgentEvent("filled", f"Filling {label}: {m.value[:50]}",
                                 {"selector": m.selector, "label": label, "value": m.value}))

    if skipped:
        events.append(AgentEvent("progress",
                                 f"{len(skipped)} file upload field{'s' if len(skipped) > 1 else ''} — you'll handle manually."))

    # Step 3: Try to auto-infer unknown fields
    if unknown:
        events.append(AgentEvent("progress", f"Trying to figure out {len(unknown)} unknown fields..."))
        try:
            inferred, still_unknown = try_autofill(unknown, page_context)
            for m in inferred:
                label = _clean_label(m.label)
                events.append(AgentEvent("filled", f"Inferred {label}: {m.value[:50]}",
                                         {"selector": m.selector, "label": label, "value": m.value}))
                # Add to auto_filled list
                auto_filled.append(m)
            unknown = still_unknown
        except Exception:
            pass

    # Step 4: Generate questions for truly unknown fields
    gap_questions = []
    for m in unknown:
        label = _clean_label(m.label)
        try:
            question = generate_question(m, page_context)
        except Exception:
            question = f"What should I put for \"{label}\"?"
        gap_questions.append({
            "selector": m.selector,
            "label": label,
            "field_type": m.field_type,
            "question": question,
        })
        events.append(AgentEvent("asking", question,
                                 {"selector": m.selector, "label": label, "field_type": m.field_type}))

    # Step 5: Draft essays
    essay_drafts = []
    for m in essays:
        label = _clean_label(m.label)
        events.append(AgentEvent("essay", f"Writing response for \"{label}\"...",
                                 {"selector": m.selector, "label": label}))
        try:
            draft = write_essay(m.label, page_context, m.max_length)
            essay_drafts.append({
                "selector": m.selector,
                "label": label,
                "draft": draft,
            })
            auto_filled.append(FieldMatch(
                selector=m.selector, field_type=m.field_type, label=m.label,
                match_type="essay", profile_key=None, value=draft,
                confidence=0.8, needs_essay=False, note="AI-drafted essay",
            ))
        except Exception as e:
            events.append(AgentEvent("error", f"Could not draft essay for {label}: {str(e)[:100]}"))

    # Build the final fill payload
    fill_matches = []
    for m in auto_filled:
        fill_matches.append({
            "selector": m.selector,
            "field_type": m.field_type,
            "label": m.label,
            "value": m.value,
            "match_type": m.match_type,
            "confidence": m.confidence,
        })

    events.append(AgentEvent("ready", f"Ready to fill {len(fill_matches)} fields. {len(gap_questions)} need your input.",
                             {
                                 "fill_matches": fill_matches,
                                 "gap_questions": gap_questions,
                                 "essay_drafts": essay_drafts,
                                 "url": url,
                                 "page_context": page_context,
                                 "total_fields": len(fields),
                             }))

    return events


def fill_with_answers(url: str, matches: list[dict], gap_answers: dict[str, str] | None = None) -> list[AgentEvent]:
    """Fill the form with all matches + user's gap answers.

    Args:
        url: Form URL
        matches: List of {selector, field_type, label, value} dicts
        gap_answers: {selector: answer} for user-provided gap answers
    """
    from .form_filler import fill_form

    events: list[AgentEvent] = []

    # Merge gap answers into matches
    if gap_answers:
        for selector, answer in gap_answers.items():
            # Check if this selector already exists in matches
            found = False
            for m in matches:
                if m["selector"] == selector:
                    m["value"] = answer
                    found = True
                    break
            if not found:
                matches.append({
                    "selector": selector,
                    "field_type": "text",
                    "label": selector,
                    "value": answer,
                    "match_type": "user_provided",
                    "confidence": 1.0,
                })

    events.append(AgentEvent("progress", "Agent is filling the form now..."))

    # Report each field being filled
    for m in matches:
        if m.get("value"):
            label = _clean_label(m.get("label", ""))
            val_preview = str(m["value"])[:40]
            events.append(AgentEvent("filling", f"Filling {label}...",
                                     {"label": label, "value": val_preview}))

    try:
        result = fill_form(url, matches)

        # Build per-field results for review screen
        field_details = []
        for fr in (result.field_results or []):
            field_details.append({
                "label": fr.label,
                "selector": fr.selector,
                "field_type": fr.field_type,
                "value": fr.value,
                "status": fr.status,
                "error_message": fr.error_message,
            })

        events.append(AgentEvent("screenshot", f"Filled {result.filled} fields, {result.skipped} skipped.",
                                 {
                                     "screenshot": result.screenshot_b64,
                                     "filled": result.filled,
                                     "skipped": result.skipped,
                                     "pages": result.pages_navigated,
                                     "errors": result.errors,
                                     "field_results": field_details,
                                 }))
    except Exception as e:
        events.append(AgentEvent("error", f"Fill failed: {str(e)[:200]}"))

    events.append(AgentEvent("done", "Form filling complete."))
    return events


def _clean_label(label: str) -> str:
    """Clean technical labels for display."""
    if not label:
        return "Unknown field"
    if label.startswith("#") or "select-" in label or "input[" in label:
        return "Form field"
    return label
