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
    from .form_filler import fill_form as execute_fill
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    read_form = None  # type: ignore
    FormField = None  # type: ignore
    execute_fill = None  # type: ignore
from .matcher import match_fields, get_unmatched, get_essay_fields, FieldMatch
from .gap_filler import generate_question, save_answer, try_autofill
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
    return {"status": "ok", "service": "formly", "version": "4.0.0"}


@app.get("/api/debug/demoqa")
def debug_demoqa():
    """Debug: open demoqa and test finding the 3 failing elements."""
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not available"}
    import asyncio
    from playwright.async_api import async_playwright

    async def _test():
        results = {}
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto("https://demoqa.com/automation-practice-form", wait_until="networkidle", timeout=30000)

            # Test 1: Can we find #currentAddress?
            try:
                el = await page.wait_for_selector("#currentAddress", timeout=5000, state="attached")
                if el:
                    tag = await el.evaluate("e => e.tagName")
                    vis = await el.is_visible()
                    results["currentAddress"] = {"found": True, "tag": tag, "visible": vis}
                else:
                    results["currentAddress"] = {"found": False, "reason": "wait_for_selector returned None"}
            except Exception as e:
                results["currentAddress"] = {"found": False, "reason": str(e)[:200]}

            # Test 2: Can we find #subjectsInput?
            try:
                el = await page.wait_for_selector("#subjectsInput", timeout=5000, state="attached")
                if el:
                    tag = await el.evaluate("e => e.tagName")
                    vis = await el.is_visible()
                    parent_class = await el.evaluate("e => e.parentElement?.className || ''")
                    results["subjectsInput"] = {"found": True, "tag": tag, "visible": vis, "parent_class": parent_class[:100]}
                else:
                    results["subjectsInput"] = {"found": False, "reason": "wait_for_selector returned None"}
            except Exception as e:
                results["subjectsInput"] = {"found": False, "reason": str(e)[:200]}

            # Test 3: Can we find radio buttons?
            try:
                radios = await page.query_selector_all('input[name="gender"]')
                radio_info = []
                for r in radios:
                    info = await r.evaluate("""e => ({
                        id: e.id, value: e.value, checked: e.checked,
                        visible: e.offsetParent !== null,
                        label: e.labels?.[0]?.textContent?.trim() || 'no label',
                        labelFor: e.labels?.[0]?.htmlFor || 'none'
                    })""")
                    radio_info.append(info)
                results["genderRadios"] = {"found": len(radios), "details": radio_info}
            except Exception as e:
                results["genderRadios"] = {"found": 0, "reason": str(e)[:200]}

            # Test 4: Try get_by_label for Male
            try:
                loc = page.get_by_label("Male", exact=True)
                count = await loc.count()
                results["getByLabelMale"] = {"count": count}
                if count > 0:
                    await loc.first.check(force=True)
                    results["getByLabelMale"]["checked"] = True
            except Exception as e:
                results["getByLabelMale"] = {"error": str(e)[:200]}

            # Test 5: What fields does form_reader find?
            try:
                all_ids = await page.evaluate("""() => {
                    return [...document.querySelectorAll('input, textarea, select')].map(e => ({
                        id: e.id, name: e.name, type: e.type, tag: e.tagName,
                        visible: e.offsetParent !== null
                    })).filter(e => e.id || e.name)
                }""")
                results["allFields"] = all_ids
            except Exception as e:
                results["allFields"] = {"error": str(e)[:200]}

            await browser.close()
        return results

    return asyncio.run(_test())


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


# ─── Photo Upload ──────────────────────────────────────

@app.post("/api/profile/photo")
async def upload_photo(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "No file provided")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("jpg", "jpeg", "png", "webp"):
        raise HTTPException(400, "Only JPG, PNG, or WebP images are supported")

    photo_path = UPLOADS_DIR / f"profile_photo.{ext}"
    with open(photo_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Save reference in profile
    db.set_profile("photo_url", str(photo_path), "personal")
    db.set_profile("has_photo", "true", "personal")

    return {"ok": True, "url": str(photo_path)}


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


# ─── Auto-Fill (Smart Inference) ───────────────────────

class AutoFillRequest(BaseModel):
    matches: list[dict]
    page_context: str = ""


@app.post("/api/forms/autofill")
def autofill_unknown(req: AutoFillRequest):
    """Try to auto-fill unknown fields using AI inference before asking the user."""
    unknown = [
        FieldMatch(
            selector=m["selector"],
            field_type=m.get("field_type", "text"),
            label=m.get("label", ""),
            match_type="unknown",
            profile_key=None,
            value=None,
            confidence=0,
            note=m.get("note", ""),
        )
        for m in req.matches
        if m.get("match_type") == "unknown" and not m.get("needs_essay")
    ]

    if not unknown:
        return {"auto_filled": [], "still_unknown": []}

    try:
        filled, remaining = try_autofill(unknown, req.page_context)
        return {
            "auto_filled": [
                {"selector": f.selector, "value": f.value, "confidence": f.confidence, "reason": f.note}
                for f in filled
            ],
            "still_unknown": [
                {"selector": f.selector, "label": f.label, "field_type": f.field_type, "note": f.note}
                for f in remaining
            ],
        }
    except Exception as e:
        return {"auto_filled": [], "still_unknown": [{"selector": m.selector, "label": m.label, "field_type": m.field_type, "note": m.note} for m in unknown]}


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


# ─── Unified Agent ─────────────────────────────────────

class AgentStartRequest(BaseModel):
    url: str


@app.post("/api/agent/start")
def agent_start(req: AgentStartRequest):
    """Start the agent — reads form, matches profile, reports what it can fill and what it needs."""
    if not PLAYWRIGHT_AVAILABLE:
        raise HTTPException(503, "Agent unavailable — browser not installed")
    from .agent import run_agent
    try:
        events = run_agent(req.url)
        return {"events": [e.to_dict() for e in events]}
    except Exception as e:
        raise HTTPException(500, f"Agent failed: {e}")


class AgentFillRequest(BaseModel):
    url: str
    matches: list[dict]
    gap_answers: dict = {}


@app.post("/api/agent/fill")
def agent_fill(req: AgentFillRequest):
    """Agent fills the form with all matches + user's gap answers."""
    if not PLAYWRIGHT_AVAILABLE:
        raise HTTPException(503, "Agent unavailable — browser not installed")
    from .agent import fill_with_answers
    try:
        events = fill_with_answers(req.url, req.matches, req.gap_answers)
        return {"events": [e.to_dict() for e in events]}
    except Exception as e:
        raise HTTPException(500, f"Agent fill failed: {e}")


# ─── Legacy Form Filling (kept for compatibility) ─────

class FillRequest(BaseModel):
    url: str
    matches: list[dict]


@app.post("/api/forms/fill")
def fill_form_endpoint(req: FillRequest):
    """Legacy endpoint — use /api/agent/fill instead."""
    if not PLAYWRIGHT_AVAILABLE:
        raise HTTPException(503, "Form filling unavailable — browser not installed")
    try:
        result = execute_fill(req.url, req.matches)
        return {
            "filled": result.filled,
            "skipped": result.skipped,
            "pages_navigated": result.pages_navigated,
            "screenshot": result.screenshot_b64,
            "errors": result.errors,
        }
    except Exception as e:
        raise HTTPException(500, f"Form filling failed: {e}")


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
