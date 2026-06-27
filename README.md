<p align="center">
  <img src="./banner.svg" width="100%" alt="NeuralHire System Initialization Animation">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/AI--Engine-Gemini%20Pro-9b59ff?style=for-the-badge&logo=google-gemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/UI--Style-Cyberpunk%20Neon-ff2d78?style=for-the-badge" alt="Style">
  <img src="https://img.shields.io/badge/Framework-Flask%20v3-00d4ff?style=for-the-badge&logo=flask&logoColor=white" alt="Flask">
</p>

---

## 🧠 System Profile Overview

**NeuralHire** is an advanced, production-grade AI recruitment engine designed to strip away traditional keyword search mechanics. By leveraging deep large language modeling frameworks, it actively parses intent, calculates behavioral momentum metrics, and ranks profiles based on conceptual tech stack matches rather than static resume strings. 

Recruiters orchestrate human capital using a unified hacker-styled dashboard featuring an interactive command execution terminal and a continuous chat companion helper protocol (**ARIA**).

> ⚡ **Hackathon Notice:** Built architecture-free using high-velocity vector alignment algorithms. Designed to run seamlessly in decoupled browser spaces via synchronized state tracking pipes.

---

## 🛠️ Core Functional Matrix

### 1. Intrinsic AI Resume Parsing
* **Strict Gatekeeping Architecture:** When candidates transmit files through the portal, the core engine executes a real-time validation sweep. 
* **Dynamic Analysis Filters:** If profiles drop below threshold parameters for the required vacancy description layout, the deployment is blocked instantly to keep pipeline databases completely clean.

### 2. ARIA Copilot System Interface
* **Real-time Pipeline Tracking:** ARIA acts as a direct link to the underlying database state. 
* **Live Query Capabilities:** Ask questions in natural human text regarding current vacancy profiles, pipeline limits, or high-performance targets, and receive structured evaluation breakdowns.

### 3. Integrated Operations Terminal
* Complete operations command interface supporting streamlined execution scripts:

| Script Syntax | Operation Objective |
| :--- | :--- |
| `/help` | Pull explicit terminal system documentation tracks. |
| `/clear` | Wipe out visual console history records from display buffers. |
| `/search [skill]` | Isolate network files matching specialized framework strings. |
| `/rank` | Sort candidate data profiles descending based on computed AI match indexes. |

---

# (only for zip file, prebuilt file only needs running builder.py)

## Make a virtual environment to avoid dependencies conflict.

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
