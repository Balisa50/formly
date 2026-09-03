# Formly

**Fill once. Apply anywhere.**

Autonomous AI agent that fills any online application form on your behalf. Job applications, scholarship forms, university admissions, visa applications, grants - anything with a form.

## How It Works

1. **Build your profile once** - Upload your CV (PDF) or enter details manually. Formly parses it with LLM extraction and stores everything in a persistent SQLite database.

2. **Paste any form URL** - Formly opens it with Playwright, reads every field (label, type, options, character limits), and understands what the form is asking for.

3. **Field matching** - An LLM matches form fields to your profile data by meaning rather than by exact label, so "Academic Background" reaches education and "Previous Employment" reaches work history.

4. **Conversational gap filling** - When information is missing, Formly asks naturally:
   > "This scholarship wants your National ID number - I don't have that yet. What is it?"

   Every answer is saved permanently. The same question is never asked twice.

5. **Essay generation** - For personal statements and open-ended questions, Formly drafts a response that references the specific opportunity and the background already in your profile.

6. **Human review gate** - Full preview of every filled field before anything is submitted. Edit any answer. You explicitly approve.

7. **Submission** - Playwright fills and submits the form. CAPTCHAs are detected and the user is asked to solve them manually.

## Architecture

```
formly/
    formly/
        api.py             # FastAPI app the dashboard talks to
        agent.py           # Orchestrates a fill: read, match, ask, write
        config.py          # Paths, env, constants
        db.py              # Profile store, grows with every form
        groq_client.py     # LLM REST wrapper
        cv_parser.py       # PDF CV to structured profile via LLM
        form_reader.py     # Playwright form field extraction
        vision_agent.py    # Screenshot fallback when the DOM is unreadable
        matcher.py         # LLM semantic field matching
        gap_filler.py      # Conversational Q&A for missing data
        essay_writer.py    # Essay and personal statement generation
        form_filler.py     # Playwright form filling and submission
    dashboard/             # Next.js UI
        app/profile/       # Profile management
        app/fill/          # The form-filling flow
        app/history/       # Application log
    Dockerfile             # Deploys to Render
```

### Why Conversational Gap Filling?

A form will always contain something the profile has never seen. The options are to skip the field, to fail, or to ask. Formly asks, one question at a time, at the point the field comes up:

- **One question at a time**, rather than a list of thirty at the start
- **The question names the form**, so it is clear what the answer is for
- **Answers are written back to the profile**, so the same field is only ever asked once
- **The profile grows with use.** By the sixth form there is usually nothing left to ask

### Database Design

The profile DB uses a hybrid approach:
- **Key-value `profile` table** - Absorbs any field type without schema changes. When a new form asks for "emergency contact phone", it gets stored as a new key.
- **Structured tables** for work, education, skills - These are naturally one-to-many and benefit from their own schema.
- **Essay archive** - Every generated essay is saved for style consistency and reference.
- **Application log** - Full submission history with field snapshots.

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

3. **Auto-match**: Name, email, phone, nationality, education - all filled from profile. 12 fields matched instantly.

4. **Gap filling conversation**:
   - "This scholarship asks for your CGPA - I couldn't find it in your CV. Can you provide it?" → User answers "3.7/4.0" → Saved permanently.
   - "They want to know your household income range. Options are: Below $10,000, $10,000-$30,000, $30,000-$50,000, Above $50,000. Which one?" → User picks → Saved.

5. **Essay generation**: "Why do you deserve this scholarship?" - Formly writes a 500-word response referencing the user's specific achievements, the scholarship organization's mission, and their career goals. User edits two sentences and approves.

6. **Preview**: All 18 fields shown with confidence indicators (green/yellow/red). User approves.

7. **Submit**: Playwright fills the form and clicks submit. Application logged in History.

Next time the user applies for something, Formly already knows their CGPA and income range. The profile grows smarter with every use.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Groq API key for LLaMA 3 access |

## Stack

- **Streamlit** - Chat UI and dashboard
- **Playwright** - Browser automation for form reading and submission
- **Groq LLaMA 3.3 70B** - CV parsing, semantic matching, essay writing
- **PyPDF2** - PDF text extraction
- **SQLite** - Persistent profile database

## License

MIT
