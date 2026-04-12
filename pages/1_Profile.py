"""Profile management — CV upload + manual editing."""
import streamlit as st
from pathlib import Path

from formly import db
from formly.cv_parser import parse_cv
from formly.config import UPLOADS_DIR

st.set_page_config(page_title="Profile — Formly", page_icon="👤", layout="wide")
st.title("Your Profile")
st.caption("Upload your CV to get started, or add details manually. Everything is saved permanently.")

# ─── CV Upload ────────────────────────────────────────────

st.subheader("Upload CV")
uploaded = st.file_uploader("Upload your CV (PDF)", type=["pdf"])

if uploaded:
    # Save to disk
    cv_path = UPLOADS_DIR / uploaded.name
    cv_path.write_bytes(uploaded.read())

    with st.spinner("Parsing your CV..."):
        try:
            data = parse_cv(cv_path)
            st.success(f"Extracted {len(data.get('work_experience', []))} jobs, "
                       f"{len(data.get('education', []))} education entries, "
                       f"{len(data.get('skills', []))} skills")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to parse CV: {e}")

st.divider()

# ─── Profile Completeness ────────────────────────────────

profile = db.get_all_profile()
work = db.get_all_work()
education = db.get_all_education()
skills = db.get_all_skills()

core_fields = ["first_name", "last_name", "email", "phone", "nationality"]
filled = sum(1 for f in core_fields if profile.get(f))
completeness = int(filled / len(core_fields) * 100)
st.progress(completeness / 100, text=f"Profile completeness: {completeness}%")

# ─── Personal Details ─────────────────────────────────────

st.subheader("Personal Details")

col1, col2 = st.columns(2)
with col1:
    first_name = st.text_input("First Name", value=profile.get("first_name", ""))
    email = st.text_input("Email", value=profile.get("email", ""))
    nationality = st.text_input("Nationality", value=profile.get("nationality", ""))
    phone = st.text_input("Phone", value=profile.get("phone", ""))
with col2:
    last_name = st.text_input("Last Name", value=profile.get("last_name", ""))
    dob = st.text_input("Date of Birth", value=profile.get("date_of_birth", ""))
    address = st.text_input("Address", value=profile.get("address", ""))
    linkedin = st.text_input("LinkedIn", value=profile.get("linkedin", ""))

if st.button("Save Personal Details", type="primary"):
    for key, val in [
        ("first_name", first_name), ("last_name", last_name),
        ("email", email), ("phone", phone),
        ("nationality", nationality), ("date_of_birth", dob),
        ("address", address), ("linkedin", linkedin),
    ]:
        if val.strip():
            db.set_profile(key, val, "personal")
    st.success("Saved!")
    st.rerun()

# ─── Work Experience ──────────────────────────────────────

st.divider()
st.subheader("Work Experience")

for job in work:
    with st.expander(f"{job['title']} at {job['company']} ({job['start_date']} - {job['end_date'] or 'Present'})"):
        st.write(job.get("description", ""))
        if st.button("Delete", key=f"del_work_{job['id']}"):
            db.delete_work(job["id"])
            st.rerun()

with st.expander("Add Work Experience"):
    w_company = st.text_input("Company", key="w_company")
    w_title = st.text_input("Job Title", key="w_title")
    w_col1, w_col2 = st.columns(2)
    with w_col1:
        w_start = st.text_input("Start Date", key="w_start", placeholder="2023-01")
    with w_col2:
        w_end = st.text_input("End Date", key="w_end", placeholder="Present")
    w_desc = st.text_area("Description", key="w_desc")
    if st.button("Add Job"):
        if w_company or w_title:
            db.add_work(w_company, w_title, w_start, w_end, w_desc)
            st.success("Added!")
            st.rerun()

# ─── Education ────────────────────────────────────────────

st.divider()
st.subheader("Education")

for edu in education:
    with st.expander(f"{edu['degree']} in {edu['field']} — {edu['institution']}"):
        if edu.get("gpa"):
            st.write(f"GPA: {edu['gpa']}")
        if st.button("Delete", key=f"del_edu_{edu['id']}"):
            db.delete_education(edu["id"])
            st.rerun()

with st.expander("Add Education"):
    e_inst = st.text_input("Institution", key="e_inst")
    e_degree = st.text_input("Degree", key="e_degree")
    e_field = st.text_input("Field of Study", key="e_field")
    e_col1, e_col2 = st.columns(2)
    with e_col1:
        e_start = st.text_input("Start Date", key="e_start", placeholder="2020-09")
    with e_col2:
        e_end = st.text_input("End Date", key="e_end", placeholder="2024-06")
    e_gpa = st.text_input("GPA", key="e_gpa")
    if st.button("Add Education"):
        if e_inst or e_degree:
            db.add_education(e_inst, e_degree, e_field, e_start, e_end, e_gpa)
            st.success("Added!")
            st.rerun()

# ─── Skills ───────────────────────────────────────────────

st.divider()
st.subheader("Skills")

skill_cols = st.columns(4)
for i, skill in enumerate(skills):
    with skill_cols[i % 4]:
        badge = f"**{skill['name']}** ({skill.get('category', '')})"
        st.markdown(badge)
        if st.button("×", key=f"del_skill_{skill['id']}"):
            db.delete_skill(skill["id"])
            st.rerun()

with st.expander("Add Skill"):
    s_name = st.text_input("Skill Name", key="s_name")
    s_cat = st.selectbox("Category", ["technical", "language", "soft"], key="s_cat")
    s_prof = st.selectbox("Proficiency", ["beginner", "intermediate", "advanced"], key="s_prof")
    if st.button("Add Skill"):
        if s_name:
            db.add_skill(s_name, s_cat, s_prof)
            st.success("Added!")
            st.rerun()

# ─── All Profile Keys ────────────────────────────────────

st.divider()
with st.expander("All Profile Data (raw)"):
    by_cat = db.get_profile_by_category()
    for cat, fields in by_cat.items():
        st.markdown(f"**{cat.upper()}**")
        for k, v in fields.items():
            st.text(f"  {k}: {v}")
