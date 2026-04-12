"""Application history — every form you've ever filled."""
import json
import streamlit as st

from formly import db

st.set_page_config(page_title="History — Formly", page_icon="📊", layout="wide")
st.title("Application History")

applications = db.get_all_applications()

if not applications:
    st.info("No applications yet. Go to Fill Form to get started!")
else:
    # Stats
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Applications", len(applications))
    with col2:
        submitted = sum(1 for a in applications if a["status"] == "submitted")
        st.metric("Submitted", submitted)
    with col3:
        if len(applications) > 0:
            rate = int(submitted / len(applications) * 100)
            st.metric("Completion Rate", f"{rate}%")

    st.divider()

    # Application list
    for app in applications:
        status_emoji = {
            "draft": "📝",
            "filled": "✏️",
            "previewed": "👀",
            "submitted": "✅",
            "failed": "❌",
            "captcha_detected": "🔒",
        }.get(app["status"], "❓")

        with st.expander(
            f"{status_emoji} {app.get('title', app['url'][:60])} — {app['status']} — {app['created_at'][:10]}"
        ):
            st.markdown(f"**URL:** {app['url']}")
            st.markdown(f"**Status:** {app['status']}")
            st.markdown(f"**Created:** {app['created_at']}")
            if app.get("submitted_at"):
                st.markdown(f"**Submitted:** {app['submitted_at']}")

            # Show filled fields
            if app.get("fields_json"):
                try:
                    fields = json.loads(app["fields_json"])
                    if fields:
                        st.markdown("**Filled Fields:**")
                        for label, value in fields.items():
                            if len(str(value)) > 100:
                                st.markdown(f"**{label}:**")
                                st.text(value)
                            else:
                                st.markdown(f"**{label}:** {value}")
                except json.JSONDecodeError:
                    pass
