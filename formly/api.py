"""Formly — FastAPI REST API wrapping all modules."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .config import UPLOADS_DIR
from .cv_parser import parse_cv
try:
    from .form_reader import read_form, FormField
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    read_form = None  # type: ignore
    FormField = None  # type: ignore
from .matcher import match_fields, get_unmatched, get_essay_fields, FieldMatch
from .gap_filler import generate_question, save_answer
from .essay_writer import write_essay

app = FastAPI(title="Formly", description="Autonomous form filling agent", version="0.1.0")

import os

dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[dashboard_url, "http://localhost:3000", "https://formly-dashboard.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health ─────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "formly"}


# ─── Profile ────────────────────────────────────────────

@app.get("/api/profile")
def get_profile():
    return {
        "personal": db.get_all_profile(),
        "work": db.get_all_work(),
        "education": db.get_all_education(),
        "skills": db.get_all_skills(),
    }


@app.get("/api/profile/full")
def get_full_profile():
    return db.get_full_profile()


@app.get("/api/profile/completeness")
def get_completeness():
    profile = db.get_all_profile()
    core = ["first_name", "last_name", "email", "phone", "nationality"]
    filled = sum(1 for f in core if profile.get(f))
    return {"completeness": int(filled / len(core) * 100), "filled": filled, "total": len(core)}


class ProfileField(BaseModel):
    key: str
    value: str
    category: str = "personal"


@app.post("/api/profile")
def set_profile_field(field: ProfileField):
    db.set_profile(field.key, field.value, field.category)
    return {"ok": True}


@app.post("/api/profile/batch")
def set_profile_batch(fields: list[ProfileField]):
    for f in fields:
        if f.value.strip():
            db.set_profile(f.key, f.value, f.category)
    return {"ok": True, "saved": len(fields)}


@app.delete("/api/profile/{key}")
def delete_profile_field(key: str):
    db.delete_profile(key)
    return {"ok": True}


# ─── CV Upload ──────────────────────────────────────────

@app.post("/api/profile/cv")
async def upload_cv(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    cv_path = UPLOADS_DIR / file.filename
    with open(cv_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        data = parse_cv(cv_path)
        return {
            "ok": True,
            "message": "CV parsed successfully! Your details have been filled in.",
            "extracted": {
                "personal": {k: v for k, v in data.items() if isinstance(v, str) and v},
                "work": len(data.get("work_experience", [])),
                "education": len(data.get("education", [])),
                "skills": len(data.get("skills", [])),
                "languages": len(data.get("languages", [])),
            },
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to parse CV: {e}")


# ─── Work Experience ────────────────────────────────────

class WorkInput(BaseModel):
    company: str
    title: str
    start_date: str = ""
    end_date: str = ""
    description: str = ""


@app.post("/api/profile/work")
def add_work(work: WorkInput):
    id = db.add_work(work.company, work.title, work.start_date, work.end_date, work.description)
    return {"ok": True, "id": id}


@app.delete("/api/profile/work/{id}")
def delete_work(id: int):
    db.delete_work(id)
    return {"ok": True}


# ─── Education ──────────────────────────────────────────

class EducationInput(BaseModel):
    institution: str
    degree: str
    field: str = ""
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""


@app.post("/api/profile/education")
def add_education(edu: EducationInput):
    id = db.add_education(edu.institution, edu.degree, edu.field, edu.start_date, edu.end_date, edu.gpa)
    return {"ok": True, "id": id}


@app.delete("/api/profile/education/{id}")
def delete_education(id: int):
    db.delete_education(id)
    return {"ok": True}


# ─── Skills ─────────────────────────────────────────────

class SkillInput(BaseModel):
    name: str
    category: str = "technical"
    proficiency: str = "intermediate"


@app.post("/api/profile/skills")
def add_skill(skill: SkillInput):
    db.add_skill(skill.name, skill.category, skill.proficiency)
    return {"ok": True}


@app.delete("/api/profile/skills/{id}")
def delete_skill(id: int):
    db.delete_skill(id)
    return {"ok": True}


# ─── Form Scanning ──────────────────────────────────────

class ScanRequest(BaseModel):
    url: str


@app.post("/api/forms/scan")
def scan_form(req: ScanRequest):
    if not PLAYWRIGHT_AVAILABLE:
        raise HTTPException(503, "Form scanning is temporarily unavailable — Playwright browser not installed on this server.")
    try:
        fields, page_context = read_form(req.url)
        return {
            "fields": [
                {
                    "selector": f.selector,
                    "field_type": f.field_type,
                    "label": f.label,
                    "placeholder": f.placeholder,
                    "required": f.required,
                    "options": f.options,
                    "max_length": f.max_length,
                }
                for f in fields
            ],
            "page_context": page_context,
            "count": len(fields),
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to scan form: {e}")


# ─── Field Matching ─────────────────────────────────────

class MatchRequest(BaseModel):
    url: str
    fields: list[dict]
    page_context: str = ""


@app.post("/api/forms/match")
def match_form_fields(req: MatchRequest):
    form_fields = [
        FormField(
            selector=f["selector"],
            field_type=f["field_type"],
            label=f["label"],
            placeholder=f.get("placeholder", ""),
            required=f.get("required", False),
            options=f.get("options", []),
            max_length=f.get("max_length"),
        )
        for f in req.fields
    ]

    try:
        matches = match_fields(form_fields, req.page_context)
        return {
            "matches": [
                {
                    "selector": m.selector,
                    "field_type": m.field_type,
                    "label": m.label,
                    "match_type": m.match_type,
                    "profile_key": m.profile_key,
                    "value": m.value,
                    "confidence": m.confidence,
                    "needs_essay": m.needs_essay,
                    "note": m.note,
                }
                for m in matches
            ],
            "auto_filled": sum(1 for m in matches if m.value and m.confidence >= 0.7),
            "needs_input": sum(1 for m in matches if m.match_type == "unknown"),
            "needs_essay": sum(1 for m in matches if m.needs_essay),
        }
    except Exception as e:
        raise HTTPException(500, f"Matching failed: {e}")


# ─── Gap Filling ────────────────────────────────────────

class GapQuestionRequest(BaseModel):
    label: str
    field_type: str
    selector: str
    page_context: str = ""


@app.post("/api/forms/gap-question")
def get_gap_question(req: GapQuestionRequest):
    field = FieldMatch(
        selector=req.selector,
        field_type=req.field_type,
        label=req.label,
        match_type="unknown",
        profile_key=None,
        value=None,
        confidence=0,
    )
    question = generate_question(field, req.page_context)
    return {"question": question}


class GapAnswerRequest(BaseModel):
    label: str
    selector: str
    field_type: str
    answer: str


@app.post("/api/forms/gap-answer")
def save_gap_answer(req: GapAnswerRequest):
    field = FieldMatch(
        selector=req.selector,
        field_type=req.field_type,
        label=req.label,
        match_type="unknown",
        profile_key=None,
        value=None,
        confidence=0,
    )
    save_answer(field, req.answer)
    return {"ok": True, "saved_to_profile": True}


# ─── Essay Writing ──────────────────────────────────────

class EssayRequest(BaseModel):
    prompt: str
    page_context: str = ""
    max_length: Optional[int] = None


@app.post("/api/forms/essay")
def generate_essay(req: EssayRequest):
    try:
        essay = write_essay(req.prompt, req.page_context, req.max_length)
        return {"essay": essay}
    except Exception as e:
        raise HTTPException(500, f"Essay generation failed: {e}")


# ─── Applications ───────────────────────────────────────

@app.get("/api/applications")
def list_applications():
    apps = db.get_all_applications()
    for a in apps:
        if a.get("fields_json"):
            try:
                a["fields"] = json.loads(a["fields_json"])
            except json.JSONDecodeError:
                a["fields"] = {}
        else:
            a["fields"] = {}
    return apps


class ApplicationLog(BaseModel):
    url: str
    title: str = ""
    fields: dict = {}


@app.post("/api/applications")
def log_application(req: ApplicationLog):
    id = db.log_application(req.url, req.title, req.fields)
    return {"ok": True, "id": id}


@app.patch("/api/applications/{id}")
def update_application(id: int, status: str, fields: Optional[dict] = None):
    db.update_application(id, status, fields)
    return {"ok": True}


# ─── Stats ──────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    apps = db.get_all_applications()
    profile = db.get_all_profile()
    work = db.get_all_work()
    education = db.get_all_education()
    skills = db.get_all_skills()
    return {
        "total_applications": len(apps),
        "submitted": sum(1 for a in apps if a["status"] == "submitted"),
        "profile_fields": len(profile),
        "work_entries": len(work),
        "education_entries": len(education),
        "skills_count": len(skills),
    }
