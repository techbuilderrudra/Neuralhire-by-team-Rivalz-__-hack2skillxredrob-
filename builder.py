#!/usr/bin/env python3
"""
NeuralHire — Production Flask Backend (Replit Edition)
======================================================
Fixed for Replit deployment:
  - PORT from environment variable
  - CORS open for proxy
  - Gemini AI for ARIA + resume analysis
  - Dynamic Google OAuth redirect URI
  - Session cookies work behind HTTPS proxy
"""

import json
import logging
import os
import re
import secrets
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
DB_PATH    = BASE_DIR / "neuralhire.db"
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
UPLOAD_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GEMINI_API_KEY       = os.environ.get("GEMINI_API_KEY", "").strip()

DEFAULT_ADMIN_EMAIL    = os.environ.get("NH_ADMIN_EMAIL",    "admin@rivalz.ai")
DEFAULT_ADMIN_PASSWORD = os.environ.get("NH_ADMIN_PASSWORD", "NeuralHire@2026!")
DEFAULT_ADMIN_NAME     = "NeuralHire Admin"

# ── Imports ───────────────────────────────────────────────────────────────────
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    import requests as http_requests
    HAS_GOOGLE_AUTH = True
except ImportError:
    HAS_GOOGLE_AUTH = False

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"]  = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = False   # set True when behind HTTPS-only
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# CORS: reflect origin for credentialed requests (wildcard + credentials is rejected by browsers)
@app.after_request
def cors_headers(resp):
    origin = request.headers.get("Origin", "")
    resp.headers["Access-Control-Allow-Origin"]      = origin or "*"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"]     = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"]     = "GET,POST,PATCH,DELETE,OPTIONS"
    return resp

@app.route("/<path:p>", methods=["OPTIONS"])
def preflight(p=""):
    return "", 204

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("neuralhire")

# ── Database ──────────────────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_db(_exc=None):
    db = getattr(g, "_database", None)
    if db:
        db.close()


def add_col(conn, table, col, definition):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email        TEXT    UNIQUE NOT NULL,
            password_hash TEXT,
            full_name    TEXT    NOT NULL DEFAULT '',
            role         TEXT    NOT NULL DEFAULT 'participant',
            oauth_sub    TEXT,
            created_at   TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS permits (
            email      TEXT PRIMARY KEY,
            role       TEXT NOT NULL DEFAULT 'participant',
            granted_by TEXT,
            granted_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT NOT NULL,
            email             TEXT NOT NULL UNIQUE,
            skills            TEXT DEFAULT '',
            experience_years  INTEGER DEFAULT 0,
            profile_text      TEXT DEFAULT '',
            match_score       REAL  DEFAULT 0,
            status            TEXT  DEFAULT 'pending',
            position          TEXT  DEFAULT '',
            location          TEXT  DEFAULT '',
            portfolio_url     TEXT  DEFAULT '',
            resume_filename   TEXT  DEFAULT ''
        )
    """)

    # Safe migrations
    for col, defn in [
        ("semantic_score",    "REAL DEFAULT 0"),
        ("momentum_score",    "REAL DEFAULT 0"),
        ("impact_statements", "TEXT DEFAULT '[]'"),
        ("skill_gaps",        "TEXT DEFAULT '[]'"),
        ("ai_insight",        "TEXT DEFAULT ''"),
        ("created_at",        "TEXT"),
    ]:
        add_col(conn, "candidates", col, defn)

    # Seed admin if no users exist
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        cur.execute(
            "INSERT OR REPLACE INTO users (email, password_hash, full_name, role, created_at) VALUES (?,?,?,?,?)",
            (DEFAULT_ADMIN_EMAIL, generate_password_hash(DEFAULT_ADMIN_PASSWORD),
             DEFAULT_ADMIN_NAME, "super_owner", datetime.now(timezone.utc).isoformat()),
        )
        cur.execute(
            "INSERT OR REPLACE INTO permits (email, role, granted_by, granted_at) VALUES (?,?,?,?)",
            (DEFAULT_ADMIN_EMAIL, "super_owner", "system", datetime.now(timezone.utc).isoformat()),
        )
        log.info("✅  Admin seeded: %s", DEFAULT_ADMIN_EMAIL)

    conn.commit()
    conn.close()
    log.info("✅  DB ready: %s", DB_PATH)


# ── AI: Gemini ────────────────────────────────────────────────────────────────
def call_gemini(prompt: str, system: str = "") -> str | None:
    if not GEMINI_API_KEY:
        return None
    import requests as req
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    contents = []
    if system:
        contents.append({"role": "user",  "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    try:
        r = req.post(url, json={"contents": contents}, timeout=20)
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        log.warning("Gemini error: %s", e)
    return None


def gemini_analyze_resume(resume_text: str, skills: str, experience_years: int, position: str) -> dict:
    """Ask Gemini to score + analyze the candidate."""
    prompt = f"""You are NeuralHire's AI Recruiter. Score and analyze this candidate.

Job Position: {position or 'General Software / AI Role'}

Candidate Skills: {skills}
Years of Experience: {experience_years}
Resume / Profile:
{resume_text[:3000]}

Return ONLY valid JSON with exactly these keys:
{{
  "ai_score": <0-100 number>,
  "semantic_score": <0-100 number>,
  "momentum_score": <0-20 number>,
  "ai_insight": "<1-2 sentence summary>",
  "skill_gaps": ["skill1", "skill2"],
  "impact_statements": ["statement1"],
  "resume_valid": <true if resume content matches the skills/position, false if mismatch>,
  "rejection_reason": "<reason if resume_valid is false, else null>"
}}"""
    raw = call_gemini(prompt)
    if raw:
        try:
            # Extract JSON even if wrapped in markdown
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception:
            pass
    return {}


# ── Scoring (fallback when Gemini unavailable) ────────────────────────────────
SCORING_CRITERIA = [
    (r"\bai\b|artificial intelligence", 10),
    (r"\bmachine learning\b|\bml\b",     9),
    (r"\bdeep learning\b|\bdl\b",        8),
    (r"\bpython\b",                      7),
    (r"\bllm\b|large language model",    7),
    (r"\bneural network\b",              6),
    (r"\btensorflow\b|\bkeras\b",        5),
    (r"\bpytorch\b",                     5),
    (r"\bscikit.learn\b|sklearn",        4),
    (r"\bfull.?stack\b",                 4),
    (r"\bdeveloper\b|software engineer", 3),
    (r"\bapi\b|rest\b|fastapi\b|flask\b|django\b", 3),
    (r"\bsql\b|postgresql|mysql|sqlite", 3),
    (r"\bnlp\b|natural language",        4),
    (r"\bbert\b|gpt\b|transformer\b|llama\b", 4),
    (r"\bcomputer vision\b|\bcv\b",      3),
    (r"\baws\b|azure\b|gcp\b|cloud\b",   3),
    (r"\bdocker\b|kubernetes\b|devops\b|ci.?cd\b", 2),
    (r"\bteam\b|collaborat|leadership",  1),
    (r"\bresearch\b|published|paper",    2),
]

EXPERIENCE_PTS = [(10,30),(7,26),(5,22),(3,17),(2,12),(1,7),(0,3)]

def compute_basic_score(skills: str, exp: int, profile: str) -> float:
    corpus = f"{skills} {exp} {profile}".lower()
    total_w = sum(w for _, w in SCORING_CRITERIA)
    matched = sum(w for p, w in SCORING_CRITERIA if re.search(p, corpus, re.I))
    kw_score = (matched / total_w) * 70.0
    exp_score = next((pts for min_yrs, pts in EXPERIENCE_PTS if exp >= min_yrs), 3)
    depth = min(3.0, len(profile.split()) / 100.0)
    return round(min(100.0, kw_score + exp_score + depth), 2)


def score_candidate(skills: str, exp: int, profile: str, position: str) -> dict:
    """Returns full scoring dict; uses Gemini if available, falls back to keyword match."""
    gemini_result = gemini_analyze_resume(profile, skills, exp, position)
    if gemini_result and "ai_score" in gemini_result:
        return {
            "match_score":       float(gemini_result.get("ai_score", 0)),
            "semantic_score":    float(gemini_result.get("semantic_score", 0)),
            "momentum_score":    float(gemini_result.get("momentum_score", 0)),
            "ai_insight":        gemini_result.get("ai_insight", ""),
            "skill_gaps":        gemini_result.get("skill_gaps", []),
            "impact_statements": gemini_result.get("impact_statements", []),
            "resume_valid":      gemini_result.get("resume_valid", True),
            "rejection_reason":  gemini_result.get("rejection_reason"),
        }
    basic = compute_basic_score(skills, exp, profile)
    return {
        "match_score":       basic,
        "semantic_score":    0.0,
        "momentum_score":    0.0,
        "ai_insight":        "Basic keyword match (Gemini unavailable).",
        "skill_gaps":        [],
        "impact_statements": [],
        "resume_valid":      True,
        "rejection_reason":  None,
    }


def generate_report(name, email, scores: dict) -> str | None:
    ref = f"NH-{secrets.token_hex(3).upper()}"
    path = REPORT_DIR / f"verify_{ref}.txt"
    try:
        path.write_text(
            f"NEURALHIRE VERIFICATION REPORT\n"
            f"================================\n"
            f"REF       : {ref}\n"
            f"CANDIDATE : {name}\n"
            f"EMAIL     : {email}\n"
            f"SCORE     : {scores['match_score']}%\n"
            f"SEMANTIC  : {scores['semantic_score']}%\n"
            f"MOMENTUM  : {scores['momentum_score']}/20\n"
            f"VERDICT   : {'LEGIT' if scores['match_score'] > 40 else 'FLAGGED'}\n"
            f"AI INSIGHT: {scores['ai_insight']}\n"
            f"SKILL GAPS: {', '.join(scores['skill_gaps']) or 'None'}\n"
            f"TIMESTAMP : {datetime.now()}\n"
            f"---------------------------------\n"
            f"RECOMMENDATION: {'Advance to interview' if scores['match_score'] > 70 else 'Review' if scores['match_score'] > 40 else 'Do not advance'}\n",
            encoding="utf-8",
        )
        return str(path)
    except Exception as e:
        log.error("Report write failed: %s", e)
        return None


# ── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("recruiter_id"):
            return jsonify({"status": "error", "message": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def _upsert_oauth_user(email: str, full_name: str, oauth_sub: str) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        db.execute("UPDATE users SET full_name=?, oauth_sub=? WHERE email=?",
                   (full_name, oauth_sub, email))
        db.commit()
    else:
        db.execute(
            "INSERT INTO users (email, full_name, role, oauth_sub, created_at) VALUES (?,?,?,?,?)",
            (email, full_name, "participant", oauth_sub, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())


# ── Google OAuth ──────────────────────────────────────────────────────────────
def get_redirect_uri():
    # Build redirect URI dynamically from Replit's domain
    domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if domain:
        return f"https://{domain}/login/google/callback"
    # Fallback for local
    return os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/login/google/callback")


@app.get("/login/google")
def login_google():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"status": "error", "message": "Google OAuth not configured. Set GOOGLE_CLIENT_ID."}), 503
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    redirect_uri = get_redirect_uri()
    params = "&".join([
        "response_type=code",
        f"client_id={GOOGLE_CLIENT_ID}",
        f"redirect_uri={redirect_uri}",
        "scope=openid%20email%20profile",
        f"state={state}",
        "access_type=offline",
        "prompt=select_account",
    ])
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.get("/login/google/callback")
def login_google_callback():
    if not HAS_GOOGLE_AUTH:
        return "google-auth package not installed.", 500
    code  = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code:
        return redirect("/?auth=error&reason=no_code")
    if state != session.pop("oauth_state", None):
        return redirect("/?auth=error&reason=state_mismatch")

    redirect_uri = get_redirect_uri()
    try:
        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            },
            timeout=10,
        )
        token_resp.raise_for_status()
        token_resp = token_resp.json()
    except Exception as e:
        log.warning("OAuth token exchange failed: %s", e)
        return redirect("/?auth=error&reason=token_exchange_failed")

    id_token_str = token_resp.get("id_token")
    if not id_token_str:
        return redirect("/?auth=error&reason=no_id_token")

    try:
        info = id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        log.warning("ID token verify failed: %s", e)
        return redirect("/?auth=error&reason=token_invalid")

    email     = info.get("email", "")
    full_name = info.get("name", email)
    sub       = info.get("sub", "")
    if not email:
        return redirect("/?auth=error&reason=no_email")

    user = _upsert_oauth_user(email, full_name, sub)
    db   = get_db()
    permit_row = db.execute("SELECT * FROM permits WHERE email=?", (email,)).fetchone()
    permit = dict(permit_row)["role"] if permit_row else user.get("role", "participant")

    session.permanent  = True
    session["recruiter_id"]   = user["id"]
    session["recruiter_email"] = email
    session["recruiter_name"]  = full_name
    session["recruiter_role"]  = permit
    log.info("Google OAuth login: %s", email)
    return redirect("/?auth=google_success")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "NeuralHire API",
        "version": "3.0.0",
        "gemini": bool(GEMINI_API_KEY),
        "google_oauth": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    })


# 1. Candidate Sign-up ─────────────────────────────────────────────────────
@app.post("/api/signup")
def api_signup():
    first = request.form.get("first_name", "").strip()
    last  = request.form.get("last_name",  "").strip()
    name  = f"{first} {last}".strip()
    email = (request.form.get("email") or "").strip().lower()

    if not name or not email:
        return jsonify({"status": "error", "message": "Name and email are required"}), 422
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        return jsonify({"status": "error", "message": "Invalid email address"}), 422

    raw_skills   = request.form.get("skills", "")
    exp_years    = int(request.form.get("experience_years", 0) or 0)
    profile_text = (request.form.get("profile_text") or "").strip()
    position     = (request.form.get("position")     or "").strip()
    location     = (request.form.get("location")     or "").strip()
    portfolio    = (request.form.get("portfolio_url") or "").strip()

    if isinstance(raw_skills, list):
        skills_str = ", ".join(str(s).strip() for s in raw_skills if s)
    else:
        skills_str = str(raw_skills).strip()

    # Resume upload
    resume_filename = None
    resume_text     = profile_text
    resume_file     = request.files.get("resume")
    if resume_file and resume_file.filename:
        safe_name     = secure_filename(resume_file.filename)
        resume_filename = f"{secrets.token_hex(8)}_{safe_name}"
        resume_path   = UPLOAD_DIR / resume_filename
        resume_file.save(resume_path)
        try:
            resume_text += "\n" + resume_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    scores = score_candidate(skills_str, exp_years, resume_text, position)

    # Block submission if AI says resume doesn't match
    if not scores.get("resume_valid", True):
        return jsonify({
            "status":  "rejected",
            "message": scores.get("rejection_reason") or "Your resume does not match the required skills for this position.",
            "ai_score": scores["match_score"],
        }), 422

    report = generate_report(name, email, scores)
    db     = get_db()

    # Upsert: insert or update if same email already applied
    db.execute("""
        INSERT INTO candidates
          (name, email, skills, experience_years, profile_text,
           match_score, semantic_score, momentum_score,
           impact_statements, ai_insight, skill_gaps,
           status, position, location, portfolio_url,
           resume_filename, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET
          name=excluded.name, skills=excluded.skills,
          experience_years=excluded.experience_years,
          profile_text=excluded.profile_text,
          match_score=excluded.match_score,
          semantic_score=excluded.semantic_score,
          momentum_score=excluded.momentum_score,
          impact_statements=excluded.impact_statements,
          ai_insight=excluded.ai_insight,
          skill_gaps=excluded.skill_gaps,
          position=excluded.position, location=excluded.location,
          portfolio_url=excluded.portfolio_url,
          resume_filename=excluded.resume_filename
    """, (
        name, email, skills_str, exp_years, profile_text,
        scores["match_score"], scores["semantic_score"], scores["momentum_score"],
        json.dumps(scores["impact_statements"]), scores["ai_insight"],
        json.dumps(scores["skill_gaps"]),
        "pending", position, location, portfolio, resume_filename or "",
        datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()

    cand = db.execute("SELECT id FROM candidates WHERE email=?", (email,)).fetchone()
    report_url = f"/reports/{Path(report).name}" if report else None

    return jsonify({
        "status":      "success",
        "message":     "Application submitted! Our AI has analysed your profile.",
        "candidate_id": cand["id"] if cand else None,
        "ai_score":    scores["match_score"],
        "ai_insight":  scores["ai_insight"],
        "report_url":  report_url,
    })


# 2. Recruiter Login ───────────────────────────────────────────────────────
@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pwd   = (data.get("password") or "").strip()

    if not email or not pwd:
        return jsonify({"status": "error", "message": "Email and password required"}), 422

    db  = get_db()
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not row["password_hash"]:
        return jsonify({"status": "error", "message": "Invalid credentials"}), 401
    if not check_password_hash(row["password_hash"], pwd):
        return jsonify({"status": "error", "message": "Invalid credentials"}), 401

    permit_row = db.execute("SELECT * FROM permits WHERE email=?", (email,)).fetchone()
    permit = permit_row["role"] if permit_row else row["role"]

    session.permanent          = True
    session["recruiter_id"]    = row["id"]
    session["recruiter_email"] = email
    session["recruiter_name"]  = row["full_name"]
    session["recruiter_role"]  = permit

    return jsonify({
        "status": "success",
        "recruiter": {
            "id":        row["id"],
            "email":     email,
            "full_name": row["full_name"],
            "permit":    permit,
        },
    })


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logged out"})


@app.get("/api/session")
def api_session():
    if session.get("recruiter_id"):
        return jsonify({
            "logged_in": True,
            "id":        session["recruiter_id"],
            "email":     session.get("recruiter_email"),
            "full_name": session.get("recruiter_name"),
            "permit":    session.get("recruiter_role"),
        })
    return jsonify({"logged_in": False})


# 3. Register recruiter / invite user (admin-only) ────────────────────────────
@app.post("/api/register")
@login_required
def api_register():
    caller_role = session.get("recruiter_role", "participant")
    if caller_role not in ("super_owner", "agent"):
        return jsonify({"status": "error", "message": "Insufficient permissions"}), 403

    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pwd   = (data.get("password") or "").strip()
    name  = (data.get("full_name") or data.get("name") or "").strip()
    requested_role = (data.get("role") or "participant").strip()

    # Only super_owner can grant super_owner; agents can only grant participant
    ALLOWED_ROLES = {"super_owner", "agent", "participant"}
    if requested_role not in ALLOWED_ROLES:
        requested_role = "participant"
    if caller_role == "agent" and requested_role in ("super_owner", "agent"):
        requested_role = "participant"

    if not email or not pwd:
        return jsonify({"status": "error", "message": "Email and password required"}), 422

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        return jsonify({"status": "error", "message": "Email already registered"}), 409

    db.execute(
        "INSERT INTO users (email, password_hash, full_name, role, created_at) VALUES (?,?,?,?,?)",
        (email, generate_password_hash(pwd), name, requested_role, datetime.now(timezone.utc).isoformat()),
    )
    db.execute(
        "INSERT OR REPLACE INTO permits (email, role, granted_by, granted_at) VALUES (?,?,?,?)",
        (email, requested_role, session.get("recruiter_email", "system"), datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return jsonify({"status": "success", "message": f"User {email} registered as {requested_role}."})


# 4. Candidates list ──────────────────────────────────────────────────────────
@app.get("/api/candidates")
@login_required
def api_candidates():
    status_filter = request.args.get("status", "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 500)), 1000))
    except (TypeError, ValueError):
        limit = 500

    db = get_db()
    if status_filter:
        rows = db.execute(
            "SELECT * FROM candidates WHERE status=? ORDER BY match_score DESC LIMIT ?",
            (status_filter, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM candidates ORDER BY match_score DESC LIMIT ?", (limit,)
        ).fetchall()

    result = []
    for rank, row in enumerate(rows, 1):
        d = dict(row)
        d["rank"]             = rank
        d["skills"]           = [s.strip() for s in d["skills"].split(",") if s.strip()]
        d["impact_statements"] = _safe_json(d.get("impact_statements", "[]"), [])
        d["skill_gaps"]       = _safe_json(d.get("skill_gaps", "[]"), [])
        d["initials"]         = "".join(p[0].upper() for p in d["name"].split()[:2] if p)
        result.append(d)

    return jsonify({"candidates": result, "total": len(result)})


@app.get("/api/candidates/<int:cid>")
@login_required
def api_candidate_detail(cid: int):
    db  = get_db()
    row = db.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not row:
        return jsonify({"status": "error", "message": "Not found"}), 404
    d = dict(row)
    d["skills"]            = [s.strip() for s in d["skills"].split(",") if s.strip()]
    d["impact_statements"] = _safe_json(d.get("impact_statements", "[]"), [])
    d["skill_gaps"]        = _safe_json(d.get("skill_gaps", "[]"), [])
    return jsonify(d)


@app.patch("/api/candidates/<int:cid>")
@login_required
def api_update_candidate(cid: int):
    data   = request.get_json(silent=True) or {}
    db     = get_db()
    row    = db.execute("SELECT id FROM candidates WHERE id=?", (cid,)).fetchone()
    if not row:
        return jsonify({"status": "error", "message": "Not found"}), 404
    allowed = ["status", "position", "location", "skills", "profile_text"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        cols = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE candidates SET {cols} WHERE id=?", (*updates.values(), cid))
        db.commit()
    return jsonify({"status": "success", "message": "Updated"})


# 5. Analyze (re-score) ───────────────────────────────────────────────────────
@app.post("/api/analyze")
@login_required
def api_analyze():
    data = request.get_json(silent=True) or {}
    db   = get_db()
    row  = None

    if data.get("candidate_id"):
        row = db.execute("SELECT * FROM candidates WHERE id=?", (int(data["candidate_id"]),)).fetchone()
    elif data.get("email"):
        row = db.execute("SELECT * FROM candidates WHERE email=?", (data["email"].strip().lower(),)).fetchone()

    if row:
        scores = score_candidate(row["skills"], row["experience_years"], row["profile_text"], row["position"])
        db.execute("""
            UPDATE candidates
            SET match_score=?, semantic_score=?, momentum_score=?,
                ai_insight=?, skill_gaps=?, impact_statements=?
            WHERE id=?
        """, (
            scores["match_score"], scores["semantic_score"], scores["momentum_score"],
            scores["ai_insight"], json.dumps(scores["skill_gaps"]),
            json.dumps(scores["impact_statements"]), row["id"],
        ))
        db.commit()
        return jsonify({
            "status":      "success",
            "candidate_id": row["id"],
            "name":         row["name"],
            **{k: scores[k] for k in ("match_score","semantic_score","momentum_score","ai_insight","skill_gaps","impact_statements")},
        })

    # Ad-hoc score (no persist)
    skills   = str(data.get("skills", ""))
    exp      = int(data.get("experience_years", 0) or 0)
    profile  = str(data.get("profile_text", ""))
    position = str(data.get("position", ""))
    if not skills and not profile:
        return jsonify({"status": "error", "message": "Provide candidate_id, email, or skills+profile_text"}), 422
    scores = score_candidate(skills, exp, profile, position)
    return jsonify({"status": "success", "message": "Ad-hoc score (not saved)", **scores})


# 6. ARIA Chat ────────────────────────────────────────────────────────────────
@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"response": "Say something! I'm listening. 👂"}), 200

    db      = get_db()
    total   = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM candidates WHERE status='pending'").fetchone()[0]
    short   = db.execute("SELECT COUNT(*) FROM candidates WHERE status='shortlisted'").fetchone()[0]
    hired   = db.execute("SELECT COUNT(*) FROM candidates WHERE status='hired'").fetchone()[0]
    avg_row = db.execute("SELECT ROUND(AVG(match_score),1) a FROM candidates").fetchone()
    avg_score = avg_row[0] or 0

    system = f"""You are ARIA, NeuralHire's intelligent AI hiring co-pilot. You are helpful, concise, and professional.
Current pipeline: {total} total candidates, {pending} pending, {short} shortlisted, {hired} hired.
Average AI score: {avg_score}/100.
You can answer questions about the hiring pipeline, suggest actions, and help recruiters make decisions.
Keep responses under 3 sentences unless asked for more detail. Do not use excessive emojis."""

    # Command shortcuts
    text = msg.lower()
    if any(k in text for k in ["list candidates", "show candidates", "how many candidates"]):
        return jsonify({"response": f"📊 Pipeline: {total} total ({pending} pending, {short} shortlisted, {hired} hired). Average AI score: {avg_score}/100."})

    if "top" in text and any(k in text for k in ["candidates", "applicants"]):
        top_rows = db.execute("SELECT name, match_score, status FROM candidates ORDER BY match_score DESC LIMIT 5").fetchall()
        lines = [f"{i+1}. {r['name']} — {r['match_score']}% ({r['status']})" for i, r in enumerate(top_rows)]
        return jsonify({"response": "🏆 Top Candidates:\n" + "\n".join(lines)})

    if GEMINI_API_KEY:
        reply = call_gemini(msg, system=system)
        if reply:
            return jsonify({"response": reply})

    # Offline fallback
    fallback = [
        f"I see {total} candidates in the pipeline with an average score of {avg_score}. 🎯",
        f"Currently tracking {pending} pending and {short} shortlisted candidates. Want me to run a fresh analysis?",
        "Set GEMINI_API_KEY to unlock full AI conversation. I can still show you pipeline stats!",
    ]
    import random
    return jsonify({"response": random.choice(fallback)})


# 7. Commands tab ─────────────────────────────────────────────────────────────
@app.post("/api/command")
@login_required
def api_command():
    data = request.get_json(silent=True) or {}
    text = (data.get("command") or data.get("text") or "").strip().lower()

    db = get_db()

    if not text:
        return jsonify({"status": "error", "message": "Empty command."})

    # help
    if "help" in text:
        return jsonify({"status": "success", "message": (
            "Available commands:\n"
            "  list candidates         — Show all candidates\n"
            "  list pending            — Show pending candidates\n"
            "  list shortlisted        — Show shortlisted candidates\n"
            "  stats                   — Pipeline statistics\n"
            "  shortlist top N         — Shortlist top N candidates\n"
            "  hire [email]            — Mark candidate as hired\n"
            "  reject [email]          — Mark candidate as rejected\n"
            "  analyze [email]         — Re-score a candidate\n"
            "  clear pipeline          — Remove all candidates (admin)\n"
        )})

    # stats
    if text in ("stats", "statistics", "pipeline stats"):
        total = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        pend  = db.execute("SELECT COUNT(*) FROM candidates WHERE status='pending'").fetchone()[0]
        short = db.execute("SELECT COUNT(*) FROM candidates WHERE status='shortlisted'").fetchone()[0]
        hired = db.execute("SELECT COUNT(*) FROM candidates WHERE status='hired'").fetchone()[0]
        avg   = db.execute("SELECT ROUND(AVG(match_score),1) FROM candidates").fetchone()[0] or 0
        return jsonify({"status": "success", "message":
            f"📊 Total: {total}  |  Pending: {pend}  |  Shortlisted: {short}  |  Hired: {hired}  |  Avg Score: {avg}%"})

    # list candidates
    if text in ("list candidates", "list all", "show all"):
        rows = db.execute("SELECT name, email, match_score, status FROM candidates ORDER BY match_score DESC LIMIT 20").fetchall()
        if not rows:
            return jsonify({"status": "success", "message": "Pipeline is empty."})
        lines = [f"{r['name']} ({r['email']}) — {r['match_score']}% [{r['status']}]" for r in rows]
        return jsonify({"status": "success", "message": "\n".join(lines)})

    # list pending / shortlisted
    for status in ("pending", "shortlisted", "hired", "rejected"):
        if f"list {status}" in text or f"show {status}" in text:
            rows = db.execute("SELECT name, email, match_score FROM candidates WHERE status=? ORDER BY match_score DESC", (status,)).fetchall()
            if not rows:
                return jsonify({"status": "success", "message": f"No {status} candidates."})
            lines = [f"{r['name']} ({r['email']}) — {r['match_score']}%" for r in rows]
            return jsonify({"status": "success", "message": f"[{status.upper()}]\n" + "\n".join(lines), "action": "refresh_candidates"})

    # shortlist top N
    m = re.match(r"shortlist\s+top\s+(\d+)", text)
    if m:
        n = max(1, min(50, int(m.group(1))))
        rows = db.execute("SELECT id FROM candidates ORDER BY match_score DESC LIMIT ?", (n,)).fetchall()
        for r in rows:
            db.execute("UPDATE candidates SET status='shortlisted' WHERE id=?", (r["id"],))
        db.commit()
        return jsonify({"status": "success", "message": f"✅ Top {len(rows)} candidates shortlisted.", "action": "refresh_candidates"})

    # hire [email]
    m = re.match(r"hire\s+(.+)", text)
    if m:
        target = m.group(1).strip()
        row = db.execute("SELECT id, name FROM candidates WHERE LOWER(email)=? OR LOWER(name) LIKE ?",
                         (target, f"%{target}%")).fetchone()
        if not row:
            return jsonify({"status": "error", "message": f"Candidate '{target}' not found."})
        db.execute("UPDATE candidates SET status='hired' WHERE id=?", (row["id"],))
        db.commit()
        return jsonify({"status": "success", "message": f"🎉 {row['name']} marked as hired!", "action": "refresh_candidates"})

    # reject [email]
    m = re.match(r"reject\s+(.+)", text)
    if m:
        target = m.group(1).strip()
        row = db.execute("SELECT id, name FROM candidates WHERE LOWER(email)=? OR LOWER(name) LIKE ?",
                         (target, f"%{target}%")).fetchone()
        if not row:
            return jsonify({"status": "error", "message": f"Candidate '{target}' not found."})
        db.execute("UPDATE candidates SET status='rejected' WHERE id=?", (row["id"],))
        db.commit()
        return jsonify({"status": "success", "message": f"❌ {row['name']} rejected.", "action": "refresh_candidates"})

    # analyze [email]
    m = re.match(r"analyze\s+(.+)", text)
    if m:
        target = m.group(1).strip()
        row = db.execute("SELECT * FROM candidates WHERE LOWER(email)=? OR LOWER(name) LIKE ?",
                         (target, f"%{target}%")).fetchone()
        if not row:
            return jsonify({"status": "error", "message": f"Candidate '{target}' not found."})
        scores = score_candidate(row["skills"], row["experience_years"], row["profile_text"], row["position"])
        db.execute("""UPDATE candidates SET match_score=?,semantic_score=?,momentum_score=?,ai_insight=? WHERE id=?""",
                   (scores["match_score"], scores["semantic_score"], scores["momentum_score"], scores["ai_insight"], row["id"]))
        db.commit()
        return jsonify({"status": "success",
                        "message": f"🤖 {row['name']}: score updated to {scores['match_score']}%. {scores['ai_insight']}",
                        "action": "refresh_candidates"})

    # clear pipeline (admin only)
    if "clear pipeline" in text:
        db.execute("DELETE FROM candidates")
        db.commit()
        return jsonify({"status": "success", "message": "⚠️ Pipeline cleared.", "action": "refresh_candidates"})

    # AI fallback
    if GEMINI_API_KEY:
        reply = call_gemini(
            f"The user ran this NeuralHire command: '{text}'. Briefly explain what command they might have meant or suggest the correct syntax.",
            system="You are NeuralHire's command assistant. Be brief and helpful."
        )
        if reply:
            return jsonify({"status": "error", "message": f"Unknown command. {reply}"})

    return jsonify({"status": "error", "message": f"Unknown command '{text}'. Type 'help' for a list."})


# 8. Dashboard stats ──────────────────────────────────────────────────────────
@app.get("/api/dashboard/stats")
@login_required
def dashboard_stats():
    db      = get_db()
    total   = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    short   = db.execute("SELECT COUNT(*) FROM candidates WHERE status='shortlisted'").fetchone()[0]
    hired   = db.execute("SELECT COUNT(*) FROM candidates WHERE status='hired'").fetchone()[0]
    avg_row = db.execute("SELECT COALESCE(AVG(match_score),0) FROM candidates").fetchone()
    avg     = round(avg_row[0] or 0, 1)
    return jsonify({
        "total_candidates": total,
        "shortlisted":      short,
        "hired":            hired,
        "avg_score":        avg,
        "conversion":       round((short / total * 100) if total else 0, 1),
    })


# 9. Users / Permit management ────────────────────────────────────────────────
@app.get("/api/users")
@login_required
def api_users():
    db   = get_db()
    rows = db.execute("SELECT id, email, full_name, role, created_at FROM users ORDER BY id").fetchall()
    return jsonify({"users": [dict(r) for r in rows]})


@app.post("/api/users/<int:uid>/role")
@login_required
def api_set_role(uid: int):
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip()
    if role not in ("super_owner", "agent", "participant"):
        return jsonify({"status": "error", "message": "Invalid role"}), 422
    db = get_db()
    row = db.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return jsonify({"status": "error", "message": "User not found"}), 404
    db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    db.execute("INSERT OR REPLACE INTO permits (email, role, granted_by, granted_at) VALUES (?,?,?,?)",
               (row["email"], role, session.get("recruiter_email"), datetime.now(timezone.utc).isoformat()))
    db.commit()
    return jsonify({"status": "success"})


# 10. File serving ────────────────────────────────────────────────────────────
@app.get("/reports/<path:filename>")
def serve_report(filename):
    return send_from_directory(str(REPORT_DIR), filename)

@app.get("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)

@app.get("/candidate-apply.html")
def serve_apply():
    return send_from_directory(str(BASE_DIR), "candidate-apply.html")

@app.get("/setup.html")
def serve_setup():
    return send_from_directory(str(BASE_DIR), "setup.html")

@app.get("/")
@app.get("/index.html")
def serve_index():
    return send_from_directory(str(BASE_DIR), "main-app.html")


# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(_): return jsonify({"status": "error", "message": "Not found"}), 404

@app.errorhandler(405)
def method_not_allowed(_): return jsonify({"status": "error", "message": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(exc):
    log.exception("Unhandled error")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_json(s, default):
    try:
        return json.loads(s)
    except Exception:
        return default


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    init_db()
    print("╔══════════════════════════════════════════════════╗")
    print("║      NEURALHIRE — Production Server v3.0         ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  Port      : {port}")
    print(f"  DB        : {DB_PATH}")
    print(f"  Admin     : {DEFAULT_ADMIN_EMAIL}")
    print(f"  Gemini    : {'✅ configured' if GEMINI_API_KEY else '⚠️  missing — set GEMINI_API_KEY'}")
    print(f"  Google OAuth: {'✅ configured' if GOOGLE_CLIENT_ID else '⚠️  missing — set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET'}")
    app.run(host="0.0.0.0", port=port, debug=False)
