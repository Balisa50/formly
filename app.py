"""Formly — Fill once. Apply anywhere."""
import streamlit as st

st.set_page_config(
    page_title="Formly",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Formly")
st.caption("Fill once. Apply anywhere.")

st.markdown("""
### How it works

1. **Build your profile** — Upload your CV or enter details manually. Formly remembers everything.
2. **Paste a form URL** — Formly reads every field on the page.
3. **Watch it fill** — Profile data matches to form fields automatically. Missing info? Formly asks you naturally.
4. **Review & submit** — Preview every answer before anything gets submitted.

Your profile grows smarter with every form you fill. The same question is never asked twice.

---

Use the sidebar to navigate between pages.
""")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Profile", "Sidebar > Profile")
with col2:
    st.metric("Fill a Form", "Sidebar > Fill Form")
with col3:
    st.metric("History", "Sidebar > History")
