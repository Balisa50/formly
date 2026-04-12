# Formly

**Fill once. Apply anywhere.**

Autonomous AI agent that fills any online application form on your behalf. Job applications, scholarship forms, university admissions, visa applications, grants — anything with a form.

## How It Works

1. **Build your profile once** — Upload your CV (PDF) or enter details manually. Formly parses it with LLM extraction and stores everything in a persistent SQLite database.

2. **Paste any form URL** — Formly opens it with Playwright, reads every field (label, type, options, character limits), and understands what the form is asking for.

3. **Intelligent matching** — Groq LLaMA 3 semantically matches form fields to your profile data. "Academic Background" maps to education. "Previous Employment" maps to work history. Context, not keywords.

4. **Conversational gap filling** — When information is missing, Formly asks naturally:
   > "This scholarship wants your National ID number — I don't have that yet. What is it?"

   Every answer is saved permanently. The same question is never asked twice.

5. **Tailored essay generation** — For personal statements and open-ended questions, Formly writes specific responses referencing the actual opportunity and your real background. No generic filler.

6. **Human review gate** — Full preview of every filled field before anything is submitted. Edit any answer. You explicitly approve.

7. **Submission** — Playwright fills and submits the form. CAPTCHAs are detected and the user is asked to solve them manually.

## Architecture

```
formly/
    formly/
        config.py          # Paths, env, constants
        db.py              # SQLite profile DB (grows with every form)
        groq_client.py     # Groq REST API wrapper
        cv_parser.py       # PDF CV -> structured profile via LLM
        form_reader.py     # Playwright form field extraction
        matcher.py         # LLM semantic field matching
        gap_filler.py      # Conversational Q&A for missing data
        essay_writer.py    # Tailored essay/statement generation
        submitter.py       # Playwright form filling + submission
    pages/
        1_Profile.py       # Profile management UI
        2_Fill_Form.py     # Chat-driven form filling flow
        3_History.py       # Application log dashboard
    app.py                 # Streamlit entry point
```

### Why Conversational Gap Filling?

Most form-filling tools fail silently on unknown fields or dump a list of 30 questions at once. Formly asks one question at a time, naturally, like a helpful assistant. This is a deliberate design choice:

- **One question at a time** — Feels like a conversation, not a survey
- **Context-aware questions** — References the specific form and what it's asking for
- **Permanent memory** — Every answer is saved to the profile DB. Fill 5 forms and by the 6th, Formly rarely needs to ask anything
- **No duplicates** — If you told Formly your CGPA once, it's stored forever

### Database Design

The profile DB uses a hybrid approach:
- **Key-value `profile` table** — Absorbs any field type without schema changes. When a new form asks for "emergency contact phone", it gets stored as a new key.
- **Structured tables** for work, education, skills — These are naturally one-to-many and benefit from their own schema.
- **Essay archive** — Every generated essay is saved for style consistency and reference.
- **Application log** — Full submission history with field snapshots.

## Quick Start

```bash
# Clone and install
git clone https://github.com/Balisa50/formly.git
cd formly
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# Add your GROQ_API_KEY to .env

# Run
streamlit run app.py
```

## Demo Walkthrough

### Filling a Scholarship Application

1. **Upload CV** on the Profile page. Formly extracts 3 jobs, 2 degrees, 12 skills automatically.

2. **Paste the scholarship URL** on Fill Form. Formly scans and finds 18 fields.

3. **Auto-match**: Name, email, phone, nationality, education — all filled from profile. 12 fields matched instantly.

4. **Gap filling conversation**:
   - "This scholarship asks for your CGPA — I couldn't find it in your CV. Can you provide it?" → User answers "3.7/4.0" → Saved permanently.
   - "They want to know your household income range. Options are: Below $10,000, $10,000-$30,000, $30,000-$50,000, Above $50,000. Which one?" → User picks → Saved.

5. **Essay generation**: "Why do you deserve this scholarship?" — Formly writes a 500-word response referencing the user's specific achievements, the scholarship organization's mission, and their career goals. User edits two sentences and approves.

6. **Preview**: All 18 fields shown with confidence indicators (green/yellow/red). User approves.

7. **Submit**: Playwright fills the form and clicks submit. Application logged in History.

Next time the user applies for something, Formly already knows their CGPA and income range. The profile grows smarter with every use.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Groq API key for LLaMA 3 access |

## Stack

- **Streamlit** — Chat UI and dashboard
- **Playwright** — Browser automation for form reading and submission
- **Groq LLaMA 3.3 70B** — CV parsing, semantic matching, essay writing
- **PyPDF2** — PDF text extraction
- **SQLite** — Persistent profile database

## License

MIT
