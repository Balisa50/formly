"""Parse a PDF CV into structured profile data using LLM extraction."""
from __future__ import annotations

import json
from pathlib import Path

from PyPDF2 import PdfReader

from . import db
from .groq_client import chat

EXTRACTION_PROMPT = """You are an expert CV parser. Extract structured data from the CV text below.

Return ONLY valid JSON with this exact structure:
{
  "first_name": "",
  "last_name": "",
  "email": "",
  "phone": "",
  "address": "",
  "nationality": "",
  "date_of_birth": "",
  "summary": "",
  "linkedin": "",
  "website": "",
  "work_experience": [
    {"company": "", "title": "", "start_date": "", "end_date": "", "description": ""}
  ],
  "education": [
    {"institution": "", "degree": "", "field": "", "start_date": "", "end_date": "", "gpa": ""}
  ],
  "skills": ["skill1", "skill2"],
  "languages": ["language1"],
  "certifications": ["cert1"],
  "achievements": ["achievement1"]
}

Rules:
- Extract ONLY what is explicitly stated in the CV. Do not invent data.
- Use empty string "" for fields not found.
- Use empty arrays [] for lists not found.
- Dates should be in YYYY-MM format when possible.
- For end_date, use "present" if the person is currently in that role/program.
"""


def extract_text(pdf_path: Path) -> str:
    """Extract raw text from all pages of a PDF."""
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def parse_cv(pdf_path: Path) -> dict:
    """Parse a CV PDF and save extracted data to the profile database.

    Returns the extracted structured data dict.
    """
    raw_text = extract_text(pdf_path)
    if not raw_text.strip():
        raise ValueError("Could not extract text from PDF. It may be image-based.")

    response = chat(
        system=EXTRACTION_PROMPT,
        user=f"CV TEXT:\n\n{raw_text}",
        temperature=0.1,
    )

    # Parse JSON from response (handle markdown code blocks)
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]

    data = json.loads(cleaned)

    # Save personal fields to profile
    personal_fields = [
        "first_name", "last_name", "email", "phone", "address",
        "nationality", "date_of_birth", "summary", "linkedin", "website",
    ]
    for field in personal_fields:
        value = data.get(field, "")
        if value:
            db.set_profile(field, value, "personal")

    # Save work experience
    for job in data.get("work_experience", []):
        if job.get("company") or job.get("title"):
            db.add_work(
                company=job.get("company", ""),
                title=job.get("title", ""),
                start_date=job.get("start_date", ""),
                end_date=job.get("end_date", ""),
                description=job.get("description", ""),
            )

    # Save education
    for edu in data.get("education", []):
        if edu.get("institution") or edu.get("degree"):
            db.add_education(
                institution=edu.get("institution", ""),
                degree=edu.get("degree", ""),
                field=edu.get("field", ""),
                start_date=edu.get("start_date", ""),
                end_date=edu.get("end_date", ""),
                gpa=edu.get("gpa", ""),
            )

    # Save skills
    for skill in data.get("skills", []):
        if skill:
            db.add_skill(skill, "technical")

    # Save languages
    for lang in data.get("languages", []):
        if lang:
            db.add_skill(lang, "language")

    # Save certifications and achievements as profile keys
    certs = data.get("certifications", [])
    if certs:
        db.set_profile("certifications", "; ".join(certs), "skills")

    achievements = data.get("achievements", [])
    if achievements:
        db.set_profile("achievements", "; ".join(achievements), "skills")

    return data
