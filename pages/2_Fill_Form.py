"""Main form-filling flow — chat-style interface."""
import json
import streamlit as st

from formly import db
from formly.form_reader import read_form, FormField
from formly.matcher import match_fields, get_unmatched, get_essay_fields, FieldMatch
from formly.gap_filler import generate_question, save_answer
from formly.essay_writer import write_essay
from formly.submitter import fill_and_submit

st.set_page_config(page_title="Fill Form — Formly", page_icon="📝", layout="wide")
st.title("Fill a Form")

# ─── Session State ────────────────────────────────────────

if "form_state" not in st.session_state:
    st.session_state.form_state = "input"  # input -> scanning -> matching -> filling -> preview -> submitting -> done
if "matches" not in st.session_state:
    st.session_state.matches = []
if "fields" not in st.session_state:
    st.session_state.fields = []
if "page_context" not in st.session_state:
    st.session_state.page_context = ""
if "url" not in st.session_state:
    st.session_state.url = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "gap_queue" not in st.session_state:
    st.session_state.gap_queue = []
if "gap_index" not in st.session_state:
    st.session_state.gap_index = 0
if "app_id" not in st.session_state:
    st.session_state.app_id = None

# ─── Step 1: URL Input ───────────────────────────────────

if st.session_state.form_state == "input":
    st.markdown("Paste the URL of any form and I'll read every field on the page.")

    url = st.text_input("Form URL", placeholder="https://forms.example.com/apply")

    if st.button("Scan Form", type="primary") and url:
        st.session_state.url = url
        st.session_state.form_state = "scanning"
        st.rerun()

# ─── Step 2: Scanning ────────────────────────────────────

if st.session_state.form_state == "scanning":
    with st.spinner(f"Reading form at {st.session_state.url}..."):
        try:
            fields, page_context = read_form(st.session_state.url)
            st.session_state.fields = fields
            st.session_state.page_context = page_context

            if not fields:
                st.error("No form fields found on this page. Make sure the URL has a form.")
                st.session_state.form_state = "input"
            else:
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": f"Found **{len(fields)} fields** on this form. Let me match them to your profile..."
                })
                st.session_state.form_state = "matching"
                st.rerun()
        except Exception as e:
            st.error(f"Failed to read form: {e}")
            st.session_state.form_state = "input"

# ─── Step 3: Matching ────────────────────────────────────

if st.session_state.form_state == "matching":
    with st.spinner("Matching fields to your profile..."):
        try:
            matches = match_fields(
                st.session_state.fields,
                st.session_state.page_context,
            )
            st.session_state.matches = matches

            # Categorize results
            auto_filled = [m for m in matches if m.value and m.confidence >= 0.7]
            low_conf = [m for m in matches if m.value and m.confidence < 0.7]
            unmatched = get_unmatched(matches)
            essays = get_essay_fields(matches)

            summary = f"**Matched {len(auto_filled)} fields** from your profile."
            if low_conf:
                summary += f" {len(low_conf)} need your review."
            if unmatched:
                summary += f" {len(unmatched)} missing — I'll ask you."
            if essays:
                summary += f" {len(essays)} need written responses."

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": summary,
            })

            # Queue up gap-filling questions
            st.session_state.gap_queue = unmatched
            st.session_state.gap_index = 0

            # Log application
            st.session_state.app_id = db.log_application(
                url=st.session_state.url,
                title=st.session_state.page_context,
            )

            if unmatched:
                st.session_state.form_state = "filling"
            elif essays:
                st.session_state.form_state = "essays"
            else:
                st.session_state.form_state = "preview"

            st.rerun()

        except Exception as e:
            st.error(f"Matching failed: {e}")
            st.session_state.form_state = "input"

# ─── Step 4: Gap Filling (conversational) ─────────────────

if st.session_state.form_state == "filling":
    # Show chat history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Get current gap question
    idx = st.session_state.gap_index
    queue = st.session_state.gap_queue

    if idx < len(queue):
        field = queue[idx]

        # Generate question if not already shown
        question_key = f"gap_q_{idx}"
        if question_key not in st.session_state:
            question = generate_question(field, st.session_state.page_context)
            st.session_state[question_key] = question
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": question,
            })
            st.rerun()

        # Show the question
        with st.chat_message("assistant"):
            st.markdown(st.session_state[question_key])

        # User input
        answer = st.chat_input("Your answer...")
        if answer:
            # Save to chat history
            st.session_state.chat_history.append({"role": "user", "content": answer})

            # Save to profile permanently
            save_answer(field, answer)

            # Update the match with the answer
            for m in st.session_state.matches:
                if m.selector == field.selector:
                    m.value = answer
                    m.confidence = 1.0
                    m.match_type = "direct"
                    break

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": f"Got it! Saved to your profile — I'll remember this for future forms."
            })

            st.session_state.gap_index += 1
            st.rerun()
    else:
        # All gaps filled — check for essays
        essays = get_essay_fields(st.session_state.matches)
        if essays:
            st.session_state.form_state = "essays"
        else:
            st.session_state.form_state = "preview"
        st.rerun()

# ─── Step 4b: Essay Generation ────────────────────────────

if st.session_state.form_state == "essays":
    st.subheader("Written Responses")

    essays = get_essay_fields(st.session_state.matches)

    for i, field in enumerate(essays):
        st.markdown(f"**{field.label}**")

        essay_key = f"essay_{i}"
        if essay_key not in st.session_state:
            with st.spinner(f"Writing response for: {field.label}..."):
                text = write_essay(
                    prompt=field.label,
                    page_context=st.session_state.page_context,
                    max_length=field.note if field.note and field.note.isdigit() else None,
                )
                st.session_state[essay_key] = text

        edited = st.text_area(
            f"Edit response for: {field.label}",
            value=st.session_state[essay_key],
            height=200,
            key=f"essay_edit_{i}",
        )
        st.session_state[essay_key] = edited

        # Update match with essay
        for m in st.session_state.matches:
            if m.selector == field.selector:
                m.value = edited
                m.needs_essay = False
                break

    if st.button("Continue to Preview", type="primary"):
        st.session_state.form_state = "preview"
        st.rerun()

# ─── Step 5: Preview ─────────────────────────────────────

if st.session_state.form_state == "preview":
    st.subheader("Review All Answers")
    st.caption("Edit anything before submitting. Nothing is sent until you approve.")

    matches = st.session_state.matches

    for i, m in enumerate(matches):
        if m.value is None:
            continue

        col1, col2 = st.columns([1, 3])
        with col1:
            confidence_color = "🟢" if m.confidence >= 0.8 else "🟡" if m.confidence >= 0.5 else "🔴"
            st.markdown(f"{confidence_color} **{m.label}**")
        with col2:
            if len(m.value) > 100:
                edited = st.text_area(
                    m.label,
                    value=m.value,
                    height=100,
                    key=f"preview_{i}",
                    label_visibility="collapsed",
                )
            else:
                edited = st.text_input(
                    m.label,
                    value=m.value,
                    key=f"preview_{i}",
                    label_visibility="collapsed",
                )
            m.value = edited

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Submit Form", type="primary"):
            st.session_state.form_state = "submitting"
            st.rerun()
    with col2:
        if st.button("Cancel"):
            st.session_state.form_state = "input"
            st.session_state.matches = []
            st.session_state.chat_history = []
            st.rerun()

# ─── Step 6: Submission ──────────────────────────────────

if st.session_state.form_state == "submitting":
    with st.spinner("Filling and submitting the form..."):
        try:
            result = fill_and_submit(
                url=st.session_state.url,
                matches=st.session_state.matches,
                auto_submit=True,
            )

            if result.get("captcha"):
                st.warning("CAPTCHA detected! Please solve it in the browser window. "
                           "The form will submit after you complete it.")

            # Update application log
            fields_snapshot = {
                m.label: m.value
                for m in st.session_state.matches
                if m.value
            }

            if st.session_state.app_id:
                db.update_application(
                    st.session_state.app_id,
                    status=result["status"],
                    fields=fields_snapshot,
                )

            st.success(f"Done! Filled {result['filled']} fields. Status: {result['status']}")

            if result.get("errors"):
                with st.expander("Errors"):
                    for err in result["errors"]:
                        st.error(f"{err['field']}: {err['error']}")

            st.session_state.form_state = "done"

        except Exception as e:
            st.error(f"Submission failed: {e}")
            st.session_state.form_state = "preview"

# ─── Done ─────────────────────────────────────────────────

if st.session_state.form_state == "done":
    st.balloons()
    st.success("Application complete! Check the History page for your records.")

    if st.button("Fill Another Form"):
        for key in ["form_state", "matches", "fields", "page_context", "url",
                     "chat_history", "gap_queue", "gap_index", "app_id"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()
