"""Generate tailored essays and personal statements using Groq LLaMA 3.

Every essay is specific to the opportunity. Every essay sounds like the
applicant wrote it themselves under time pressure — not like an AI."""
from __future__ import annotations

import json

from .groq_client import chat
from . import db

ESSAY_SYSTEM = """You are ghostwriting as this specific person. Your job is to write exactly how they would write if they had 30 minutes and were trying their best.

ABSOLUTE RULES — BREAK ANY OF THESE AND THE ESSAY FAILS:

1. Write in FIRST PERSON as the applicant. You ARE them.

2. Use their REAL details — actual university name, actual company names, actual project names, actual skills. Never make up achievements.

3. NEVER use these words or phrases (they scream "AI wrote this"):
   - leverage, synergy, passionate, driven, utilise, utilize, furthermore
   - in conclusion, it is worth noting, delve, groundbreaking, revolutionary
   - I am excited to, I am eager to, I believe I would be a great fit
   - I am confident that, it goes without saying, needless to say
   - transformative, innovative (unless quoting someone)
   - "This opportunity aligns perfectly with..."
   - "Having always been passionate about..."

4. Sound like a real person writing under pressure:
   - Start some sentences with "And" or "But" — real people do this
   - Use contractions: "I've", "didn't", "can't", "I'd"
   - Mix sentence lengths: some short. Some longer with detail.
   - One slightly informal phrase is fine — shows personality
   - Never use the same sentence structure twice in a row

5. Be SPECIFIC, not generic:
   BAD: "I have experience in data science and machine learning"
   GOOD: "I built a forecasting model for Gambian dalasi exchange rates using SARIMA — it processes CBG data daily and has been running for 3 months"

6. Reference the SPECIFIC opportunity:
   - Name the organisation, programme, or role
   - Mention something specific about them (what they do, recent news)
   - Connect YOUR specific experience to THEIR specific needs

7. Word/character limits:
   - If a limit is given, hit it within 5% — not 50 words under
   - If no limit is given, write 200-400 words

8. The "Would a human think AI wrote this?" test:
   - Read your output back. If it sounds polished, rehearsed, or template-like — rewrite
   - Real essays have personality. They tell a mini-story. They have a point of view.

Output ONLY the essay text. No explanations, no headers, no "Here is your essay"."""


def write_essay(
    prompt: str,
    page_context: str = "",
    max_length: int | None = None,
    save: bool = True,
) -> str:
    """Write a tailored essay for a specific form field."""
    profile = db.get_full_profile()

    # Get past approved essays for style consistency
    past = db.get_past_essays(3)
    past_context = ""
    if past:
        past_context = "\n\nPrevious writing by this person (match their voice):\n"
        for e in past[:2]:
            past_context += f"Q: {e['prompt'][:100]}\nA: {e['response'][:300]}...\n\n"

    length_instruction = ""
    if max_length:
        word_estimate = max(max_length // 5, 50)
        length_instruction = f"\nWORD LIMIT: Aim for exactly {word_estimate} words ({max_length} characters). Don't go under by more than 10%."
    else:
        length_instruction = "\nAim for 200-400 words. Enough to be substantial, not so much it's boring."

    user_prompt = f"""OPPORTUNITY: {page_context}

QUESTION: {prompt}
{length_instruction}

THIS PERSON'S REAL PROFILE:
{json.dumps(profile, indent=2, default=str)}
{past_context}

Write it now. Sound like them, not like an AI. Use their real details."""

    essay = chat(
        system=ESSAY_SYSTEM,
        user=user_prompt,
        temperature=0.7,
        max_tokens=max_length // 3 if max_length else 2048,
    )

    # Strip any accidental AI prefixes
    for prefix in ["Here is", "Here's", "Sure,", "Certainly", "Of course"]:
        if essay.startswith(prefix):
            essay = essay.split("\n", 1)[-1].strip()

    # Enforce length limit
    if max_length and len(essay) > max_length:
        essay = essay[:max_length].rsplit(" ", 1)[0]

    if save:
        db.save_essay(prompt=prompt, response=essay, context=page_context)

    return essay
