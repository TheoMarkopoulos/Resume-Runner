#!/usr/bin/env python3
"""
HITL Job Application Automator
===============================
Multi-page, human-in-the-loop job application pipeline.

Requirements:
    pip install anthropic playwright jinja2 python-dotenv
    playwright install chromium
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from jinja2 import Template
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeout,
)

load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent
MODEL = "claude-sonnet-4-20250514"
DB_PATH = BASE_DIR / "applications.db"
RESUME_TEMPLATE_PATH = BASE_DIR / "base_resume.html"
PROJECTS_BANK_PATH = BASE_DIR / "projects_bank.json"
URLS_PATH = BASE_DIR / "urls.txt"
COMMON_ANSWERS_PATH = BASE_DIR / "common_answers.json"
TAILORED_PDF_PATH = BASE_DIR / "tailored_resume.pdf"
TAILORED_HTML_PATH = BASE_DIR / "tailored_resume.html"

APPLICANT = {
    "first_name": "Theo",
    "last_name": "Markopoulos",
    "full_name": "Theo Markopoulos",
    "email": "theo.markopoulos7@gmail.com",
    "phone": "5107100027",
    "phone_formatted": "510-710-0027",
    "university": "University of California, Irvine",
    "degree": "Bachelor of Science",
    "major": "Computer Science",
    "graduation": "December 2026",
}

# ═══════════════════════════════════════════════════════════════════════════════
# IMMUTABLE BASE SKILLS
# These must NEVER be removed. JD-matching items get promoted to the front.
# ═══════════════════════════════════════════════════════════════════════════════

BASE_SKILLS = {
    "languages": "Python, JavaScript/TypeScript, C/C++, Java, SQL, HTML/CSS, MIPS Assembly",
    "libraries": "React, Next.js, Tailwind CSS, Node.js, Pandas, NumPy, Scikit-Learn",
    "software": "Git/GitHub, AWS, Supabase, PostgreSQL, Vite, VS Code, Linux, Xcode",
}

# ═══════════════════════════════════════════════════════════════════════════════
# SMART PROXIMITY ADDRESS — GEOGRAPHIC KEYWORD SETS
# ═══════════════════════════════════════════════════════════════════════════════

SOCAL_KEYWORDS = [
    "irvine", "los angeles", "orange county", "san diego",
    "southern california", "socal", "anaheim", "costa mesa",
    "newport beach", "long beach", "santa ana", " la ",
]
BAYAREA_KEYWORDS = [
    "san francisco", "oakland", "piedmont", "silicon valley",
    "san jose", "bay area", "palo alto", "mountain view",
    "sunnyvale", "cupertino", "berkeley", "fremont",
    "santa clara", "menlo park", "redwood city", " sf ",
]

# ═══════════════════════════════════════════════════════════════════════════════
# HARD-CODED SAFEGUARDS — checked BEFORE common_answers.json
# ═══════════════════════════════════════════════════════════════════════════════

RESIDENCY_PATTERNS = [
    "current location", "state of residence", "currently reside",
    "current state", "where do you live", "what state",
    "where are you located", "location",
]
EMPLOYMENT_PATTERNS = [
    "previously employed", "currently employed", "past employee",
    "current employee", "worked at", "employed by", "formerly employed",
    "previously worked", "work for this company", "employed here",
]

# ═══════════════════════════════════════════════════════════════════════════════
# PRIVACY FALLBACK KEYWORDS (checkbox/radio)
# ═══════════════════════════════════════════════════════════════════════════════

PRIVACY_DECLINE_KEYWORDS = [
    "decline", "not to specify", "not to answer", "n/a",
    "prefer not", "don't wish", "do not wish",
    "i don't wish", "choose not",
]

# ═══════════════════════════════════════════════════════════════════════════════
# ANTI-AI SYSTEM PROMPT
# Injected as the `system` parameter in Anthropic API calls (Phase 2 & 4).
# ═══════════════════════════════════════════════════════════════════════════════

ANTI_AI_SYSTEM_PROMPT = (
    "**SYSTEM ROLE & DIRECTIVE**\n"
    "You are an expert technical recruiter, resume optimization engine, "
    "and application ghostwriter. Your primary function is to autonomously "
    "read job descriptions, tailor the user's resume, and write highly "
    "authentic, human-sounding answers to custom job application questions. "
    "Your output must strike the perfect balance between human authenticity "
    "and algorithmic precision. You must sound like a pragmatic, "
    "results-driven computer science student—not a marketing algorithm.\n\n"
    "**MODULE 1: THE \"ANTI-AI\" VOICE & TONE**\n"
    "- **Eradicate AI Fluff:** Never use overly dramatic, flowery, or "
    "hyper-corporate AI buzzwords. Strictly ban: delve, foster, orchestrate, "
    "spearhead, synergize, revolutionize, elevate, testament, unwavering, "
    "multifaceted, in conclusion, and moreover.\n"
    "- **Direct & Grounded:** Do not use passive voice or unnecessary "
    "qualifiers. Keep language lean, direct, and factual.\n"
    "- **Vary Lengths (Burstiness):** Mix short, high-impact sentences "
    "with slightly more detailed, context-heavy ones.\n\n"
    "**MODULE 2: RESUME OPTIMIZATION PROTOCOL**\n"
    "- **Pragmatic Action Verbs:** Start bullet points with grounded, "
    "technical verbs: Engineered, Developed, Architected, Implemented, "
    "Optimized, Migrated, or Deployed.\n"
    "- **The XYZ Formula:** Structure accomplishments using: "
    "\"Accomplished [X] as measured by [Y], by doing [Z].\" "
    "Ensure metrics sound mathematically realistic.\n"
    "- **Semantic Matching:** Seamlessly integrate skills from the JD "
    "into the user's existing experience. Prove *how* the skill was used.\n\n"
    "**MODULE 3: APPLICATION QUESTION & ESSAY PROTOCOL**\n"
    "- **The \"Intern\" Mindset:** Write in the first-person (\"I\"). "
    "Strike a tone of confident capability mixed with a genuine desire "
    "to learn from senior engineers.\n"
    "- **Ditch the Formal Intros:** Never start an answer with "
    "'As a computer science student...' or 'I am thrilled to apply...'. "
    "Start directly with the answer.\n"
    "- **Show, Don't Tell:** Use the STAR method conversationally, not "
    "like a rigid template. Describe debugging or optimizing specific "
    "systems.\n"
    "- **Conversational Authenticity:** Use natural contractions "
    "(I'm, I've, didn't). It is acceptable to start occasional sentences "
    "with 'But' or 'And'.\n"
    "- **Brevity is Key:** Keep answers punchy. Short paragraphs "
    "(2-3 sentences max).\n\n"
    "**MODULE 4: HARD CONSTRAINTS**\n"
    "- **Target Roles:** Optimize primarily for 2026 Summer Internships.\n"
    "- **Core Domains:** Highlight full-stack development, systems "
    "programming, computer networks, embedded systems, and AI/ML.\n"
    "- **Education Anchor:** Frame academic context around a Bachelor of "
    "Science in Computer Science at the University of California, Irvine.\n"
    "- **Strict Exclusion:** Under no circumstances should a LinkedIn "
    "profile link be generated, implied, or included in any output."
)

# ═══════════════════════════════════════════════════════════════════════════════
# ANSI TERMINAL COLORS
# ═══════════════════════════════════════════════════════════════════════════════

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def load_urls() -> list[str]:
    if not URLS_PATH.exists():
        print(f"{RED}ERROR: {URLS_PATH} not found.{RESET}")
        sys.exit(1)
    urls = []
    for line in URLS_PATH.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    if not urls:
        print(f"{YELLOW}No URLs found in {URLS_PATH}. Exiting.{RESET}")
        sys.exit(0)
    return urls


def load_projects_bank() -> list[dict]:
    with open(PROJECTS_BANK_PATH, "r") as f:
        return json.load(f)


def load_common_answers() -> dict:
    with open(COMMON_ANSWERS_PATH, "r") as f:
        return json.load(f)


def extract_json(text: str) -> dict:
    """Robustly extract JSON from LLM response text."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON:\n{text[:500]}")


def get_best_address(jd_text: str, common_answers: dict) -> dict:
    """
    Smart Proximity Address Logic.
    Scans JD for SoCal or Bay Area keywords and returns the
    matching address dict from common_answers.json.
    Defaults to Irvine (school address).
    """
    jd_lower = jd_text.lower()
    addresses = common_answers.get("addresses", {})

    for kw in SOCAL_KEYWORDS:
        if kw in jd_lower:
            addr = addresses.get("Irvine", {})
            print(f"{DIM}    Address resolved: Irvine (SoCal match: '{kw}'){RESET}")
            return addr

    for kw in BAYAREA_KEYWORDS:
        if kw in jd_lower:
            addr = addresses.get("Piedmont", {})
            print(f"{DIM}    Address resolved: Piedmont (Bay Area match: '{kw}'){RESET}")
            return addr

    print(f"{DIM}    Address resolved: Irvine (default){RESET}")
    return addresses.get("Irvine", {})


def check_hardcoded_safeguard(question_text: str) -> str | None:
    """
    Hard-coded safeguards checked BEFORE common_answers.json.
    - Residency / location -> 'California'
    - Past/current employment with company -> 'No'
    """
    q = question_text.lower()
    if any(pat in q for pat in RESIDENCY_PATTERNS):
        return "California"
    if any(pat in q for pat in EMPLOYMENT_PATTERNS):
        return "No"
    return None


def enforce_base_skills(template_vars: dict, jd: str = "") -> dict:
    """
    Post-processing: ensures all immutable base skills are present.
    JD-PRIORITY: skills that appear in the JD get moved to the front
    of their respective list. Non-matching base skills follow. Then
    any LLM-appended skills trail at the end.
    """
    jd_lower = jd.lower()
    for category, base_str in BASE_SKILLS.items():
        key = f"technical_skills_{category}"
        current = template_vars.get(key, "")
        base_items = [s.strip() for s in base_str.split(",")]
        current_items = [s.strip() for s in current.split(",") if s.strip()]

        jd_priority = []
        jd_rest = []
        for item in base_items:
            if item.lower() in jd_lower:
                jd_priority.append(item)
            else:
                jd_rest.append(item)

        base_lower = {s.lower() for s in base_items}
        appended = [s for s in current_items if s.lower() not in base_lower]

        template_vars[key] = ", ".join(jd_priority + jd_rest + appended)

    return template_vars


# High-confidence field types filled even when no required markers exist on page
HIGH_CONFIDENCE_TYPES = {
    "first_name", "last_name", "full_name", "email", "phone", "resume_upload",
}


def get_required_reason(field: dict) -> str | None:
    """
    Expanded required-field detection. Returns a human-readable reason
    string if the field should be filled, or None if it appears optional.

    Checks (in priority order):
      1. HTML required attribute
      2. aria-required="true"
      3. Asterisk (*) in label text
      4. Asterisk (*) in placeholder text
      5. Asterisk in parent container text
      6. CSS class containing required/mandatory/star
      7. Red-colored element adjacent to the field
    """
    if field.get("required"):
        return "HTML required"
    if field.get("aria_required"):
        return "aria-required=true"
    label = field.get("label", "")
    placeholder = field.get("placeholder", "")
    if "*" in label:
        return "asterisk in label"
    if "*" in placeholder:
        return "asterisk in placeholder"
    if field.get("parent_has_asterisk"):
        return "asterisk in parent container"
    if field.get("label_class_required"):
        return "CSS class (required/mandatory/star)"
    if field.get("adjacent_red"):
        return "red marker adjacent to field"
    return None


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            company TEXT, role TEXT, pay TEXT,
            contact_email TEXT, url TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: JOB SCRAPING
# ═══════════════════════════════════════════════════════════════════════════════


async def scrape_job(page, url: str, client: AsyncAnthropic) -> dict:
    print(f"\n{CYAN}[Phase 1] Scraping:{RESET} {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        print(f"{YELLOW}  Page load timed out, proceeding with partial content.{RESET}")

    raw_text = await page.evaluate("() => document.body?.innerText || ''")
    raw_text = raw_text[:15_000]
    if not raw_text.strip():
        print(f"{RED}  No text content extracted from page.{RESET}")
        return None

    extraction_prompt = (
        "Extract the following fields from this job listing page text. "
        "Return ONLY a JSON object with these keys:\n"
        "- title: The job title\n"
        "- company: The company name\n"
        "- work_model: Remote, Hybrid, or On-site (infer; default 'Not specified')\n"
        "- pay: Salary or hourly rate (default 'Not listed')\n"
        "- contact_email: Any contact email found (default 'Not listed')\n"
        "- description: The FULL job description text including requirements, "
        "responsibilities, qualifications, and tech stack\n\n"
        f"PAGE TEXT:\n{raw_text}"
    )
    response = await client.messages.create(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": extraction_prompt}],
    )
    try:
        job_data = extract_json(response.content[0].text)
    except ValueError as e:
        print(f"{RED}  Failed to parse job data: {e}{RESET}")
        return None
    print(f"{GREEN}  Scraped:{RESET} {job_data.get('title', '?')} @ {job_data.get('company', '?')}")
    return job_data


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: LLM RESUME TAILORING
# — Immutable Base Skills with JD-Priority reordering
# — Dynamic Saturation: 3-5 projects, up to 4 bullets (extreme page fill)
# ═══════════════════════════════════════════════════════════════════════════════


async def tailor_resume(
    client: AsyncAnthropic, job_data: dict, projects_bank: list[dict]
) -> dict:
    print(f"\n{CYAN}[Phase 2] Tailoring resume for:{RESET} "
          f"{job_data.get('title')} @ {job_data.get('company')}")
    jd = job_data.get("description", "")

    tailoring_prompt = (
        "You are given a Job Description and a bank of candidate projects. "
        "Follow these instructions EXACTLY:\n\n"
        "1. MISSING KEYWORDS: Identify the top 5 missing technical keywords "
        "from the JD not already in the candidate's resume.\n\n"
        "2. DYNAMIC PROJECT SELECTION:\n"
        "   Select between 3 and 5 projects from the bank that best fit.\n"
        "   - Synthesize ONE project ONLY if fewer than 3 bank projects "
        "are relevant. Flag it with \"source\": \"synthesized\".\n"
        "   - Set \"project_count\" to the number you selected (3, 4, or 5).\n\n"
        "3. EXTREME PAGE SATURATION — BULLET EXPANSION:\n"
        "   Your goal is to FILL THE ENTIRE PAGE to the bottom margin. "
        "Zero white space at the bottom.\n"
        "   - Start with 2 bullets per project (minimum).\n"
        "   - If white space remains, add a 3rd bullet to projects.\n"
        "   - If STILL white space remains, add a 4th bullet to projects.\n"
        "   - Every bullet MUST be substantive XYZ-formula content. "
        "Never pad with filler.\n"
        "   - Include bullet_3 / bullet_4 keys ONLY for bullets you write.\n\n"
        "4. BULLET QUALITY: Use XYZ formula: "
        "\"Accomplished [X] as measured by [Y], by doing [Z].\" "
        "Each bullet is a technical, multi-part sentence. "
        "Start with pragmatic action verbs.\n\n"
        "5. IMMUTABLE BASE SKILLS — APPEND-ONLY:\n"
        "   These base skills are LOCKED. Never remove them:\n"
        f"   Languages: {BASE_SKILLS['languages']}\n"
        f"   Libraries: {BASE_SKILLS['libraries']}\n"
        f"   Software: {BASE_SKILLS['software']}\n"
        "   START each list with the exact base items, then APPEND new "
        "technologies from the JD and selected projects. Only prune "
        "APPENDED items (never base) if list would exceed ~4 lines.\n\n"
        "6. PANDA EXPRESS: Frame shift-lead experience with technical "
        "language relevant to the JD. Keep grounded — food service role.\n\n"
        "Return ONLY a strict JSON object. No markdown fences, no prose.\n"
        "Include keys for every project you selected. Omit keys for "
        "projects you did NOT select.\n"
        "{\n"
        '  "project_count": 3,\n'
        '  "missing_keywords": ["kw1","kw2","kw3","kw4","kw5"],\n'
        '  "panda_bullet_1": "...", "panda_bullet_2": "...",\n'
        '  "project_1_title": "...", "project_1_stack": "...",\n'
        '  "project_1_bullet_1": "...", "project_1_bullet_2": "...",\n'
        '  "project_1_bullet_3": "...",  // optional, to fill page\n'
        '  "project_1_bullet_4": "...",  // optional, to fill page\n'
        '  "project_1_source": "bank",\n'
        '  "project_2_title": "...", "project_2_stack": "...",\n'
        '  "project_2_bullet_1": "...", "project_2_bullet_2": "...",\n'
        '  "project_2_source": "bank",\n'
        '  "project_3_title": "...", "project_3_stack": "...",\n'
        '  "project_3_bullet_1": "...", "project_3_bullet_2": "...",\n'
        '  "project_3_source": "bank",\n'
        '  // project_4_* only if project_count >= 4\n'
        '  // project_5_* only if project_count >= 5\n'
        '  "technical_skills_languages": "Python, JavaScript/TypeScript, C/C++, Java, SQL, HTML/CSS, MIPS Assembly, <appended>",\n'
        '  "technical_skills_libraries": "React, Next.js, Tailwind CSS, Node.js, Pandas, NumPy, Scikit-Learn, <appended>",\n'
        '  "technical_skills_software": "Git/GitHub, AWS, Supabase, PostgreSQL, Vite, VS Code, Linux, Xcode, <appended>"\n'
        "}\n\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        f"PROJECTS BANK:\n{json.dumps(projects_bank, indent=2)}"
    )

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        response = await client.messages.create(
            model=MODEL, max_tokens=4096,
            system=ANTI_AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": tailoring_prompt}],
        )
        try:
            template_vars = extract_json(response.content[0].text)
            break
        except ValueError as e:
            print(f"{YELLOW}  JSON parse attempt {attempt}/{max_retries}: {e}{RESET}")
            if attempt == max_retries:
                print(f"{RED}  All parse attempts exhausted.{RESET}")
                return None

    project_count = template_vars.get("project_count", 3)
    if project_count not in (3, 4, 5):
        project_count = 3
    template_vars["project_count"] = project_count

    required = ["panda_bullet_1", "panda_bullet_2",
                "technical_skills_languages", "technical_skills_libraries",
                "technical_skills_software"]
    for i in range(1, project_count + 1):
        required += [f"project_{i}_title", f"project_{i}_stack",
                     f"project_{i}_bullet_1", f"project_{i}_bullet_2"]

    missing = [k for k in required if k not in template_vars]
    if missing:
        print(f"{RED}  LLM response missing keys: {missing}{RESET}")
        return None

    template_vars = enforce_base_skills(template_vars, jd)

    print(f"{GREEN}  Tailored.{RESET} Projects: {project_count} | "
          f"Keywords: {template_vars.get('missing_keywords', [])}")
    return template_vars


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: HTML TO PDF
# ═══════════════════════════════════════════════════════════════════════════════


async def generate_pdf(browser, template_vars: dict) -> Path:
    print(f"\n{CYAN}[Phase 3] Generating tailored PDF...{RESET}")

    all_vars = dict(template_vars)
    for i in range(1, 6):
        for suffix in ["title", "stack", "bullet_1", "bullet_2",
                        "bullet_3", "bullet_4", "source"]:
            all_vars.setdefault(f"project_{i}_{suffix}", "")

    html_template = RESUME_TEMPLATE_PATH.read_text()
    rendered_html = Template(html_template).render(**all_vars)
    TAILORED_HTML_PATH.write_text(rendered_html)

    page = await browser.new_page()
    await page.goto(f"file://{TAILORED_HTML_PATH.resolve()}", wait_until="load")
    await page.pdf(
        path=str(TAILORED_PDF_PATH), format="Letter",
        prefer_css_page_size=True, print_background=True,
    )
    await page.close()

    if TAILORED_HTML_PATH.exists():
        TAILORED_HTML_PATH.unlink()

    print(f"{GREEN}  PDF saved:{RESET} {TAILORED_PDF_PATH}")
    return TAILORED_PDF_PATH


# ═══════════════════════════════════════════════════════════════════════════════
# FORM FIELD HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def match_common_answer(question_text: str, common_answers: dict) -> str | None:
    """
    Local Fallback: normalize to lowercase, check if any key from
    common_answers.json exists as a substring within the question.
    Skips nested dict values (e.g. 'addresses').
    """
    normalized = question_text.lower()
    for key, answer in common_answers.items():
        if not isinstance(answer, str):
            continue
        if key.lower() in normalized:
            return answer
    return None


async def llm_answer_question(
    client: AsyncAnthropic, question: str, jd: str
) -> str:
    """LLM Fallback using the Anti-AI system prompt."""
    user_prompt = (
        "You are answering a custom question on a job application form. "
        "The candidate is Theo Markopoulos, a CS student at UC Irvine "
        "graduating December 2026.\n\n"
        "RULES:\n"
        "- If asking for Primary Language, pick the best match from the JD "
        "(default Python).\n"
        "- If asking for Hourly Rate, estimate a competitive 2026 intern "
        "rate based on the JD.\n"
        "- If asking for location/state of residence, answer California.\n"
        "- If asking about past/current employment with this company, answer No.\n"
        "- Otherwise, write a 2-3 sentence authentic answer.\n"
        "- Return ONLY the answer text. No preamble, no quotes.\n\n"
        f"JOB DESCRIPTION:\n{jd[:5000]}\n\n"
        f"QUESTION:\n{question}"
    )
    response = await client.messages.create(
        model=MODEL, max_tokens=1024,
        system=ANTI_AI_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


async def gather_form_fields(page) -> list[dict]:
    """Collect visible text/select/file fields with expanded required detection."""
    return await page.evaluate(
        """() => {
            function isRedish(el) {
                try {
                    const s = window.getComputedStyle(el);
                    const m = s.color.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                    if (!m) return false;
                    return parseInt(m[1]) > 180 && parseInt(m[2]) < 100 && parseInt(m[3]) < 100;
                } catch(e) { return false; }
            }

            const results = [];
            document.querySelectorAll('input, textarea, select').forEach((el, idx) => {
                if (el.offsetParent === null && el.type !== 'hidden') return;
                if (el.type === 'hidden') return;
                if (el.type === 'checkbox' || el.type === 'radio') return;

                // ── Label discovery ──
                let labelText = '';
                let labelEl = null;
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) { labelEl = lbl; labelText = lbl.innerText; }
                }
                if (!labelText) {
                    const p = el.closest('label');
                    if (p) { labelEl = p; labelText = p.innerText; }
                }
                if (!labelText) { const a = el.getAttribute('aria-label'); if (a) labelText = a; }
                if (!labelText) {
                    const prev = el.previousElementSibling;
                    if (prev && ['LABEL','SPAN','DIV','P'].includes(prev.tagName)) {
                        labelEl = prev; labelText = prev.innerText;
                    }
                }
                if (!labelText) {
                    const fs = el.closest('fieldset');
                    if (fs) {
                        const lg = fs.querySelector('legend');
                        if (lg) { labelEl = lg; labelText = lg.innerText; }
                    }
                }

                // ── aria-required ──
                const ariaReq = el.getAttribute('aria-required') === 'true';

                // ── Asterisk in parent container ──
                let parentHasAsterisk = false;
                const container = el.closest(
                    '[class*="field"], [class*="form-group"], [class*="form-row"], ' +
                    '[class*="question"], [class*="input"]'
                ) || el.parentElement;
                if (container && container !== document.body) {
                    const ct = container.textContent || '';
                    if (ct.includes('*')) parentHasAsterisk = true;
                }

                // ── CSS class check: label or container has required/mandatory/star ──
                let labelClassReq = false;
                const classTargets = [labelEl, container].filter(Boolean);
                for (const t of classTargets) {
                    const cls = (t.className || '').toLowerCase();
                    if (cls.includes('required') || cls.includes('mandatory') || cls.includes('star')) {
                        labelClassReq = true; break;
                    }
                }

                // ── Red-colored adjacent element ──
                let adjacentRed = false;
                const checkEls = [];
                if (el.previousElementSibling) checkEls.push(el.previousElementSibling);
                if (el.nextElementSibling) checkEls.push(el.nextElementSibling);
                if (labelEl) {
                    labelEl.querySelectorAll('span, abbr, i, em, sup').forEach(c => checkEls.push(c));
                }
                if (container && container !== document.body) {
                    container.querySelectorAll('span, abbr, i, em, sup').forEach(c => {
                        const txt = c.textContent.trim();
                        if (txt === '*' || txt === '* ' || txt === '*:') checkEls.push(c);
                    });
                }
                for (const ce of checkEls) {
                    if (isRedish(ce)) { adjacentRed = true; break; }
                }

                results.push({
                    index: idx, tag: el.tagName.toLowerCase(),
                    type: el.type || '', id: el.id || '', name: el.name || '',
                    placeholder: el.placeholder || '',
                    label: labelText.trim().substring(0, 500),
                    required: el.required, value: el.value || '',
                    accept: el.accept || '',
                    aria_required: ariaReq,
                    parent_has_asterisk: parentHasAsterisk,
                    label_class_required: labelClassReq,
                    adjacent_red: adjacentRed,
                    selector: el.id ? '#' + CSS.escape(el.id)
                        : el.name ? '[name="' + CSS.escape(el.name) + '"]'
                        : el.tagName.toLowerCase() + ':nth-of-type(' + (idx+1) + ')',
                });
            });
            return results;
        }"""
    )


async def gather_choice_groups(page) -> list[dict]:
    """Collect checkbox/radio groups with question text and options."""
    return await page.evaluate(
        """() => {
            const groups = {};
            document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(el => {
                if (el.offsetParent === null) return;
                const name = el.name || el.id || '';
                if (!name) return;
                const gk = el.type + '_' + name;

                if (!groups[gk]) {
                    groups[gk] = { name, type: el.type, question: '', options: [], required: el.required };
                    const fs = el.closest('fieldset');
                    if (fs) { const lg = fs.querySelector('legend'); if (lg) groups[gk].question = lg.innerText.trim(); }
                    if (!groups[gk].question) {
                        let c = el.parentElement;
                        for (let i = 0; i < 6 && c; i++) {
                            const ar = c.getAttribute('aria-label');
                            if (ar) { groups[gk].question = ar; break; }
                            const prev = c.previousElementSibling;
                            if (prev && ['LABEL','SPAN','DIV','P','H3','H4','H5'].includes(prev.tagName)) {
                                const t = prev.innerText.trim();
                                if (t.length > 2 && t.length < 500) { groups[gk].question = t; break; }
                            }
                            c = c.parentElement;
                        }
                    }
                    if (!groups[gk].question) {
                        let c = el.parentElement;
                        for (let i = 0; i < 5 && c; i++) {
                            const dl = c.getAttribute('data-label') || c.getAttribute('data-question');
                            if (dl) { groups[gk].question = dl; break; }
                            c = c.parentElement;
                        }
                    }
                }
                let optText = '';
                const pl = el.closest('label');
                if (pl) optText = pl.innerText.trim();
                if (!optText && el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) optText = lbl.innerText.trim();
                }
                if (!optText) optText = el.value;
                const sel = el.id ? '#' + CSS.escape(el.id)
                    : 'input[type="' + el.type + '"][name="' + CSS.escape(name) + '"][value="' + CSS.escape(el.value) + '"]';
                groups[gk].options.push({ value: el.value, text: optText, checked: el.checked, selector: sel });
            });
            return Object.values(groups);
        }"""
    )


def classify_field(field: dict) -> str:
    ctx = " ".join([
        field.get("label", ""), field.get("placeholder", ""),
        field.get("name", ""), field.get("id", ""),
    ]).lower()

    if field["type"] == "file":
        return "cover_letter" if ("cover" in ctx and "letter" in ctx) else "resume_upload"
    if any(kw in ctx for kw in ["first name", "first_name", "firstname", "fname"]):
        return "first_name"
    if any(kw in ctx for kw in ["last name", "last_name", "lastname", "lname", "surname"]):
        return "last_name"
    if any(kw in ctx for kw in ["full name", "your name", "candidate name"]):
        return "full_name"
    if re.search(r"\bname\b", ctx) and not any(x in ctx for x in ["user", "company", "school"]):
        if field["tag"] == "input" and field["type"] in ("text", ""):
            return "full_name"
    if "email" in ctx:
        return "email"
    if any(kw in ctx for kw in ["phone", "telephone", "mobile", "cell"]):
        return "phone"
    if any(kw in ctx for kw in ["university", "school", "college", "institution"]):
        return "university"
    if any(kw in ctx for kw in ["field of study", "major", "concentration", "area of study"]):
        return "major"
    if "degree" in ctx:
        return "degree"
    if "cover letter" in ctx:
        return "cover_letter"

    # Address fields
    if any(kw in ctx for kw in ["street address", "address line", "address 1", "address1"]):
        return "address_street"
    if re.search(r"\bcity\b", ctx) and field["tag"] in ("input", "select"):
        return "address_city"
    if re.search(r"\bstate\b", ctx) and field["tag"] == "select":
        return "address_state"
    if any(kw in ctx for kw in ["zip code", "zipcode", "postal code", "postal"]):
        return "address_zip"
    if re.search(r"\bzip\b", ctx):
        return "address_zip"

    return "custom"


async def process_choice_group(
    page, group: dict, common_answers: dict, records: list[dict]
) -> None:
    """
    Checkbox/radio handler:
      1. Match question against common_answers.json -> click matching option.
      2. Privacy fallback: click Decline / 'I don't wish to answer'.
    """
    question = group.get("question", "")
    options = group.get("options", [])
    if not options:
        return

    # --- Step 1: common_answers match ---
    local_answer = match_common_answer(question, common_answers)
    if local_answer is not None:
        local_lower = local_answer.lower().strip()
        for opt in options:
            opt_lower = opt["text"].lower().strip()
            if local_lower in opt_lower or opt_lower in local_lower:
                try:
                    await page.locator(opt["selector"]).click()
                    records.append({"question": question, "answer": opt["text"], "source": "Local"})
                    print(f"    Choice (Local): {question[:50]}... -> {opt['text'][:40]}")
                except Exception as e:
                    print(f"{YELLOW}    Could not click option: {e}{RESET}")
                return

    # --- Step 2: Privacy / Decline fallback ---
    for opt in options:
        opt_lower = opt["text"].lower()
        if any(kw in opt_lower for kw in PRIVACY_DECLINE_KEYWORDS):
            try:
                await page.locator(opt["selector"]).click()
                records.append({
                    "question": question,
                    "answer": f"{opt['text']} (privacy fallback)",
                    "source": "Local",
                })
                print(f"    Choice (Privacy): {question[:50]}... -> {opt['text'][:40]}")
            except Exception as e:
                print(f"{YELLOW}    Could not click privacy option: {e}{RESET}")
            return

    if question:
        print(f"{DIM}    Choice (Skip): {question[:60]}...{RESET}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: SINGLE-PAGE FILL (REQUIRED FIELDS ONLY)
# Expanded Asterisk Rule: required attr, aria-required, asterisk in
# label/placeholder/parent, CSS class, red marker. Falls back to
# high-confidence fields if zero required markers detected on page.
# ═══════════════════════════════════════════════════════════════════════════════


async def fill_current_page(
    page, client: AsyncAnthropic, job_data: dict, common_answers: dict
) -> list[dict]:
    """
    Fill visible fields on the current page.
    Uses expanded required-field detection. If zero required markers are
    found on the entire page, falls back to filling high-confidence fields
    (name, email, phone, resume) regardless of markers.
    Returns list of custom answer records.
    """
    print(f"\n{CYAN}  Filling current page...{RESET}")
    custom_answer_records = []
    jd = job_data.get("description", "")
    best_address = get_best_address(jd, common_answers)

    # ── Text / Select / File fields ──
    fields = await gather_form_fields(page)
    filled_count = 0
    skipped_count = 0

    # Reasonable Guess Fallback: if NO field on the page has any required
    # marker, assume the site doesn't use them and fill high-confidence fields.
    any_required = any(get_required_reason(f) is not None for f in fields)
    use_fallback = not any_required and len(fields) > 0
    if use_fallback:
        print(f"  {YELLOW}No required markers detected on page ({len(fields)} fields). "
              f"Falling back to high-confidence fields.{RESET}")
    else:
        req_count = sum(1 for f in fields if get_required_reason(f) is not None)
        print(f"  {DIM}Detected {req_count}/{len(fields)} required fields.{RESET}")

    for field in fields:
        ft = classify_field(field)
        reason = get_required_reason(field)

        if reason is None:
            # No required marker — check if fallback applies
            if use_fallback and ft in HIGH_CONFIDENCE_TYPES:
                print(f"    {DIM}[Fallback]{RESET} Filling {ft} (no markers, high-confidence)")
            else:
                label_preview = (field.get("label", "")
                                 or field.get("placeholder", "")
                                 or field.get("name", ""))[:40]
                print(f"{DIM}    Skipping [{ft}] '{label_preview}' — "
                      f"no required markers found{RESET}")
                skipped_count += 1
                continue
        else:
            print(f"{DIM}    Required [{ft}]: {reason}{RESET}")

        ft = classify_field(field)
        sel = field["selector"]
        try:
            if ft == "first_name":
                await page.locator(sel).fill(APPLICANT["first_name"])
                print(f"    Filled: First Name")
            elif ft == "last_name":
                await page.locator(sel).fill(APPLICANT["last_name"])
                print(f"    Filled: Last Name")
            elif ft == "full_name":
                await page.locator(sel).fill(APPLICANT["full_name"])
                print(f"    Filled: Full Name")
            elif ft == "email":
                await page.locator(sel).fill(APPLICANT["email"])
                print(f"    Filled: Email")
            elif ft == "phone":
                await page.locator(sel).fill(APPLICANT["phone_formatted"])
                print(f"    Filled: Phone")
            elif ft == "university":
                if field["tag"] == "select":
                    try:
                        await page.locator(sel).select_option(label=APPLICANT["university"])
                    except Exception:
                        await page.locator(sel).fill(APPLICANT["university"])
                else:
                    await page.locator(sel).fill(APPLICANT["university"])
                print(f"    Filled: University")
            elif ft == "degree":
                if field["tag"] == "select":
                    try:
                        await page.locator(sel).select_option(label=APPLICANT["degree"])
                    except Exception:
                        await page.locator(sel).fill(APPLICANT["degree"])
                else:
                    await page.locator(sel).fill(APPLICANT["degree"])
                print(f"    Filled: Degree")
            elif ft == "major":
                await page.locator(sel).fill(APPLICANT["major"])
                print(f"    Filled: Major")
            elif ft == "resume_upload":
                await page.locator(sel).set_input_files(str(TAILORED_PDF_PATH))
                print(f"    Uploaded: Resume")
            elif ft == "cover_letter":
                print(f"{DIM}    Skipped: Cover Letter{RESET}")
                skipped_count += 1
                continue

            # ── Smart Address fields ──
            elif ft == "address_street":
                await page.locator(sel).fill(best_address.get("street", ""))
                print(f"    Filled: Street ({best_address.get('city', '?')})")
            elif ft == "address_city":
                if field["tag"] == "select":
                    try:
                        await page.locator(sel).select_option(label=best_address.get("city", ""))
                    except Exception:
                        await page.locator(sel).fill(best_address.get("city", ""))
                else:
                    await page.locator(sel).fill(best_address.get("city", ""))
                print(f"    Filled: City ({best_address.get('city', '?')})")
            elif ft == "address_state":
                try:
                    await page.locator(sel).select_option(value=best_address.get("state", "CA"))
                except Exception:
                    try:
                        await page.locator(sel).select_option(label="California")
                    except Exception:
                        await page.locator(sel).fill(best_address.get("state", "CA"))
                print(f"    Filled: State")
            elif ft == "address_zip":
                await page.locator(sel).fill(best_address.get("zip", ""))
                print(f"    Filled: Zip ({best_address.get('zip', '?')})")

            elif ft == "custom":
                label = field.get("label", "").strip()
                if not label or len(label) < 3:
                    continue
                if field["tag"] not in ("input", "textarea", "select"):
                    continue
                if field["type"] in ("submit", "button"):
                    continue
                if field.get("value", "").strip():
                    continue

                # 1. Hard-coded safeguards (residency, employment)
                safeguard = check_hardcoded_safeguard(label)
                if safeguard is not None:
                    if field["tag"] == "select":
                        try:
                            await page.locator(sel).select_option(label=safeguard)
                        except Exception:
                            await page.locator(sel).fill(safeguard)
                    else:
                        await page.locator(sel).fill(safeguard)
                    custom_answer_records.append({"question": label, "answer": safeguard, "source": "Safeguard"})
                    print(f"    Custom (Safeguard): {label[:60]}...")
                    filled_count += 1
                    continue

                # 2. Local fallback (common_answers.json)
                local_answer = match_common_answer(label, common_answers)
                if local_answer is not None:
                    if field["tag"] == "select":
                        try:
                            await page.locator(sel).select_option(label=local_answer)
                        except Exception:
                            await page.locator(sel).fill(local_answer)
                    else:
                        await page.locator(sel).fill(local_answer)
                    custom_answer_records.append({"question": label, "answer": local_answer, "source": "Local"})
                    print(f"    Custom (Local): {label[:60]}...")
                    filled_count += 1
                    continue

                # 3. LLM fallback
                print(f"    Custom (LLM): Drafting for: {label[:60]}...")
                try:
                    llm_answer = await llm_answer_question(client, label, jd)
                    if field["tag"] == "select":
                        try:
                            await page.locator(sel).select_option(label=llm_answer)
                        except Exception:
                            await page.locator(sel).fill(llm_answer)
                    else:
                        await page.locator(sel).fill(llm_answer)
                    custom_answer_records.append({"question": label, "answer": llm_answer, "source": "LLM"})
                except Exception as e:
                    print(f"{RED}    LLM answer failed: {e}{RESET}")
                    custom_answer_records.append({"question": label, "answer": f"[ERROR: {e}]", "source": "LLM-Failed"})

            filled_count += 1
        except Exception as e:
            print(f"{YELLOW}    Could not fill {ft} ({sel}): {e}{RESET}")

    # ── Checkbox & Radio groups ──
    choice_groups = await gather_choice_groups(page)
    if choice_groups:
        for group in choice_groups:
            q = group.get("question", "")
            grp_required = group.get("required") or "*" in q
            if not grp_required and not use_fallback:
                print(f"{DIM}    Skipping choice [{q[:40]}] — no required markers{RESET}")
                skipped_count += 1
                continue
            await process_choice_group(page, group, common_answers, custom_answer_records)

    print(f"{GREEN}  Page filled.{RESET} {filled_count} required field(s), "
          f"{skipped_count} optional skipped.")
    return custom_answer_records


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: MULTI-PAGE HITL REVIEW
# [C] Fill Page  — fill required fields on the current visible page
# [F] Finish & Log — mark as complete in applications.db, clean up PDF
# [N] Abort — skip this application entirely
# ═══════════════════════════════════════════════════════════════════════════════


def display_resume_review(job_data: dict, template_vars: dict):
    """Print the HITL review panel showing tailored resume summary."""
    print(f"\n{'=' * 65}")
    print(f"{BOLD}{CYAN}  HITL REVIEW — TAILORED RESUME SUMMARY{RESET}")
    print(f"{'=' * 65}")

    print(f"\n  {BOLD}Target Role:{RESET}    {job_data.get('title', '?')}")
    print(f"  {BOLD}Company:{RESET}        {job_data.get('company', '?')}")
    print(f"  {BOLD}Work Model:{RESET}     {job_data.get('work_model', '?')}")
    print(f"  {BOLD}Pay:{RESET}            {job_data.get('pay', '?')}")
    print(f"  {BOLD}Contact:{RESET}        {job_data.get('contact_email', '?')}")

    project_count = template_vars.get("project_count", 3)
    print(f"\n  {BOLD}Selected Projects ({project_count}):{RESET}")
    for i in range(1, project_count + 1):
        title = template_vars.get(f"project_{i}_title", "?")
        source = template_vars.get(f"project_{i}_source", "bank")
        bc = sum(1 for b in range(1, 5) if template_vars.get(f"project_{i}_bullet_{b}"))
        tag = f"{RED}{BOLD}[SYNTHESIZED]{RESET}" if source == "synthesized" else f"{GREEN}[BANK]{RESET}"
        print(f"    {i}. {tag} {title} ({bc} bullets)")

    keywords = template_vars.get("missing_keywords", [])
    if keywords:
        print(f"\n  {BOLD}Injected Keywords:{RESET} {', '.join(keywords)}")


async def hitl_loop(
    page, client: AsyncAnthropic, job_data: dict,
    template_vars: dict, common_answers: dict
) -> bool:
    """
    Interactive multi-page HITL loop.
    [C] Fill Page — fill required fields on the current visible page, stay paused
    [F] Finish & Log — mark application as complete
    [N] Abort — skip this application
    Returns True if completed, False if aborted.
    """
    all_custom_answers = []
    loop = asyncio.get_event_loop()

    # Show resume summary once
    display_resume_review(job_data, template_vars)

    while True:
        # Show accumulated custom answers
        if all_custom_answers:
            print(f"\n  {BOLD}Custom Answers ({len(all_custom_answers)}):{RESET}")
            for rec in all_custom_answers:
                src = rec["source"]
                color = GREEN if src == "Local" else (CYAN if src == "Safeguard" else YELLOW)
                print(f"    {color}[{src}]{RESET} Q: {rec['question'][:70]}")
                print(f"           A: {rec['answer'][:120]}")

        print(f"\n{'─' * 65}")
        print(f"  {BOLD}[C]{RESET} Fill Page   "
              f"{BOLD}[F]{RESET} Finish & Log   "
              f"{BOLD}[N]{RESET} Abort")
        print(f"{'─' * 65}")

        choice = await loop.run_in_executor(
            None, lambda: input("  Your choice: ").strip().upper()
        )

        if choice == "C":
            records = await fill_current_page(page, client, job_data, common_answers)
            all_custom_answers.extend(records)
            print(f"\n  {YELLOW}Page filled. Navigate to the next page in the browser, "
                  f"then choose [C] again or [F] to finish.{RESET}")

        elif choice == "F":
            return True

        elif choice == "N":
            return False

        else:
            print(f"  {YELLOW}Invalid input. Enter C, F, or N.{RESET}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: LOGGING & CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════


def log_application(job_data: dict, url: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO applications (timestamp,company,role,pay,contact_email,url) VALUES (?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), job_data.get("company", ""),
         job_data.get("title", ""), job_data.get("pay", ""),
         job_data.get("contact_email", ""), url),
    )
    conn.commit()
    conn.close()
    print(f"{GREEN}  Application logged to {DB_PATH}{RESET}")


def cleanup():
    if TAILORED_PDF_PATH.exists():
        TAILORED_PDF_PATH.unlink()
        print(f"{DIM}  Cleaned up {TAILORED_PDF_PATH.name}{RESET}")
    if TAILORED_HTML_PATH.exists():
        TAILORED_HTML_PATH.unlink()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════


async def main():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{RED}ERROR: ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.{RESET}")
        sys.exit(1)

    init_db()
    urls = load_urls()
    projects_bank = load_projects_bank()
    common_answers = load_common_answers()
    client = AsyncAnthropic(api_key=api_key)

    print(f"\n{BOLD}HITL Job Application Automator{RESET}")
    print(f"{'─' * 40}")
    print(f"  URLs to process: {len(urls)}")
    print(f"  Projects in bank: {len(projects_bank)}")
    print(f"  Common answers loaded")
    print(f"  Model: {MODEL}")
    print(f"{'─' * 40}\n")

    async with async_playwright() as pw:
        # Headless browser for scraping and PDF generation
        headless_browser = await pw.chromium.launch(headless=True)
        headless_context = await headless_browser.new_context()

        for i, url in enumerate(urls, 1):
            print(f"\n{'=' * 65}")
            print(f"{BOLD}  JOB {i}/{len(urls)}{RESET}")
            print(f"{'=' * 65}")

            try:
                # ── Phase 1: Scrape JD (headless) ──
                scrape_page = await headless_context.new_page()
                job_data = await scrape_job(scrape_page, url, client)
                await scrape_page.close()
                if not job_data:
                    print(f"{RED}  Skipping: could not scrape job data.{RESET}")
                    continue

                # ── Phase 2: Tailor Resume ──
                template_vars = await tailor_resume(client, job_data, projects_bank)
                if not template_vars:
                    print(f"{RED}  Skipping: resume tailoring failed.{RESET}")
                    continue

                # ── Phase 3: Generate PDF ──
                pdf_path = await generate_pdf(headless_browser, template_vars)
                if not pdf_path or not pdf_path.exists():
                    print(f"{RED}  Skipping: PDF generation failed.{RESET}")
                    continue

                # ── Phase 4: Open headed browser for application ──
                headed_browser = await pw.chromium.launch(headless=False)
                headed_context = await headed_browser.new_context()
                app_page = await headed_context.new_page()
                try:
                    await app_page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await app_page.wait_for_timeout(2000)
                except PlaywrightTimeout:
                    print(f"{YELLOW}  Application page load timed out.{RESET}")

                # Split Scrape & Apply: if the form isn't on this page,
                # prompt the user to navigate to it manually.
                form_fields = await gather_form_fields(app_page)
                if len(form_fields) < 2:
                    print(f"\n  {YELLOW}No application form detected on this page.{RESET}")
                    print(f"  {BOLD}Navigate to the application form in the browser, "
                          f"then press Enter to start autofilling.{RESET}")
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("  Press Enter to continue..."))

                # ── Phase 5: Multi-Page HITL Loop ──
                completed = await hitl_loop(
                    app_page, client, job_data, template_vars, common_answers
                )

                # ── Phase 6: Log & Cleanup ──
                if completed:
                    log_application(job_data, url)
                    cleanup()
                    print(f"{GREEN}{BOLD}  Application logged and complete.{RESET}")
                else:
                    print(f"\n{RED}  Application aborted.{RESET}")
                    cleanup()

                await headed_context.close()
                await headed_browser.close()

            except Exception as e:
                print(f"{RED}  Unexpected error processing {url}: {e}{RESET}")
                cleanup()
                continue

        await headless_context.close()
        await headless_browser.close()

    print(f"\n{BOLD}All jobs processed.{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
