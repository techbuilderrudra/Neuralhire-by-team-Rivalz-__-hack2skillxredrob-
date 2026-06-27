# NeuralHire 🧠

AI-powered recruitment platform — candidates apply, the AI reads and scores their resumes in real time, and recruiters manage the full pipeline through a cyberpunk dashboard with an AI co-pilot (ARIA).

---

## Quick Start (3 steps)

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure your API keys

Edit the `.env` file and fill in:

| Key | Where to get it |
|-----|----------------|
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey (free) |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → OAuth 2.0 Client IDs |
| `GOOGLE_CLIENT_SECRET` | Same as above |
| `NH_SECRET_KEY` | Any long random string |

> **Google OAuth setup:** In Google Cloud Console, add this to *Authorized redirect URIs*:
> `http://localhost:5000/login/google/callback`

### 3. Run the server

```bash
python builder.py
```

Open your browser at **http://localhost:5000**

---

## Default Admin Login

- Email: `admin@neuralhire.ai`  (or whatever you set in `.env`)
- Password: `NeuralHire@2026!`

---

## Pages

| URL | Description |
|-----|-------------|
| `http://localhost:5000/` | Recruiter dashboard (login, ARIA, pipeline) |
| `http://localhost:5000/candidate-apply.html` | Candidate application portal |
| `http://localhost:5000/setup.html` | Setup wizard |

---

## Features

- **AI Resume Screening** — Gemini reads each resume and scores it 0-100 against the job description. If the resume doesn't match, the candidate can't submit.
- **ARIA Chat** — AI co-pilot powered by Gemini; knows your live pipeline stats.
- **Commands Terminal** — type commands like `shortlist top 5`, `hire john@example.com`, `stats`, `help`.
- **Google OAuth** — sign in with Google (requires Google Cloud credentials in `.env`).
- **Email/Password Auth** — works without Google OAuth.
- **Candidate Pipeline** — view, filter, sort, and update candidate status.
- **AI Re-scoring** — click "Engage Analyze Engine" to re-score all candidates.

---

## Commands (type in the ARIA chat or Commands palette)

```
help                  List all commands
stats                 Pipeline statistics
list candidates       Show all candidates
list pending          Filter by status
list shortlisted
list hired
shortlist top N       Auto-shortlist top N by AI score
hire [email]          Mark candidate as hired
reject [email]        Mark candidate as rejected
analyze [email]       Re-run AI scoring on one candidate
clear pipeline        Remove all candidates (admin only)
```

---

## File Structure

```
neuralhire/
├── builder.py           ← Flask backend (all API routes)
├── main-app.html        ← Recruiter dashboard
├── candidate-apply.html ← Candidate portal
├── setup.html           ← Setup wizard
├── requirements.txt     ← Python dependencies
├── .env                 ← Your API keys (never commit this)
├── neuralhire.db        ← SQLite database (auto-created on first run)
├── uploads/             ← Uploaded resumes
└── reports/             ← AI verification report files
```

---

## Troubleshooting

**"Invalid credentials" on first login**
→ The admin account is created on first boot. Make sure you're using the email/password from `.env`.

**ARIA gives offline responses**
→ `GEMINI_API_KEY` is missing or wrong in `.env`. Get a free key at https://aistudio.google.com/app/apikey

**Google sign-in fails**
→ Make sure `http://localhost:5000/login/google/callback` is in your OAuth 2.0 Authorized redirect URIs on Google Cloud Console.

**Port already in use**
→ The app auto-detects the next free port starting from 5000.
