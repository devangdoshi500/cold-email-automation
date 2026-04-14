#!/usr/bin/env python3
"""
email_pipeline.py
Cold email pipeline for internship outreach.

Setup:
  1. pip install -r requirements.txt
  2. export ANTHROPIC_API_KEY=sk-...
  3. export GMAIL_SENDER=you@gmail.com
  4. Download credentials.json from Google Cloud Console (see SETUP.md)
  5. python email_pipeline.py
"""

import csv
import json
import os
import re
import time
import base64
import pickle
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup
import anthropic
from google.auth.transport.requests import Request as GoogleRequest
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SENDER_EMAIL      = os.environ.get("GMAIL_SENDER", "")

# Set to True (or export AUTO_SEND=true) to skip the interactive review prompt
# and send every email automatically. Disabled by default.
AUTO_SEND = os.environ.get("AUTO_SEND", "false").lower() == "true"

BASE_DIR            = Path(__file__).parent
COMPANIES_FILE      = BASE_DIR / "companies.csv"
TOKEN_FILE          = BASE_DIR / "token.pickle"
CREDS_FILE          = BASE_DIR / "credentials.json"
RESUME_FILE         = BASE_DIR / "Resume.pdf"        # rename your resume file to this
PROFILE_CACHE_FILE  = BASE_DIR / "profile_cache.txt" # auto-generated, do not edit manually

GMAIL_SCOPES   = ["https://mail.google.com/"]


# ── Customize these for your own outreach ─────────────────────────────────────
YOUR_NAME     = "[YOUR NAME]"
YOUR_CONTEXT  = "[e.g. 'a high school senior in Atlanta and an incoming Georgia Tech CS student']"
YOUR_RESEARCH = "[e.g. 'I recently completed an ML research internship with a first-author IEEE paper']"
YOUR_ROLES    = "[e.g. 'software engineering, AI/ML, or data science']"
YOUR_SIGN_OFF = "[YOUR NAME]"

EMAIL_SUBJECT = "[Customize your subject line]"

EMAIL_TEMPLATE = """\
Hello,

My name is {your_name}, and I'm {your_context}. {your_research}.

I'm currently looking for summer internship opportunities in {your_roles}. \
I'd love to contribute to real engineering work while learning from your team. \
I'm especially interested in {{company_name}} because of {{personalized_line}}

I've attached my resume for reference and would be happy to share any \
additional information.

Would you be open for a quick conversation to see if there might be \
opportunities at your company this summer?

Sincerely,
{your_sign_off}""".format(
    your_name=YOUR_NAME,
    your_context=YOUR_CONTEXT,
    your_research=YOUR_RESEARCH.rstrip("."),
    your_roles=YOUR_ROLES,
    your_sign_off=YOUR_SIGN_OFF,
)

CSV_FIELDS = ["name", "website", "contact_email", "description",
              "personalized_line", "status", "scheduled_time"]
# status values: pending | sent | skip | scheduled (legacy) | gmail_scheduled

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Seed companies ────────────────────────────────────────────────────────────
# Add your target companies here. They will be auto-loaded into companies.csv
# on first run. You can also edit companies.csv directly.

SEED_COMPANIES = [
    # {"name": "Example Company", "website": "https://www.example.com"},
]

# ── CSV helpers ───────────────────────────────────────────────────────────────

def load_companies():
    if not COMPANIES_FILE.exists():
        return []
    with open(COMPANIES_FILE, newline="", encoding="utf-8") as f:
        companies = list(csv.DictReader(f))
    # backward-compat: older CSVs won't have scheduled_time
    for c in companies:
        c.setdefault("scheduled_time", "")
    return companies


def save_companies(companies):
    with open(COMPANIES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(companies)


def seed_csv():
    existing = load_companies()
    seen = {c["name"].lower() for c in existing}
    added = []
    for c in SEED_COMPANIES:
        if c["name"].lower() not in seen:
            added.append({
                "name": c["name"],
                "website": c["website"],
                "contact_email": "",
                "description": "",
                "personalized_line": "",
                "status": "pending",
                "scheduled_time": "",
            })
            seen.add(c["name"].lower())
    if added:
        all_companies = existing + added
        save_companies(all_companies)
        print(f"[+] Added {len(added)} new companies to companies.csv")
        return all_companies
    return existing


def add_company(name, website):
    """Add a single company to the CSV interactively."""
    companies = load_companies()
    if any(c["name"].lower() == name.lower() for c in companies):
        print(f"[!] {name} already exists in companies.csv")
        return
    companies.append({
        "name": name,
        "website": website,
        "contact_email": "",
        "description": "",
        "personalized_line": "",
        "status": "pending",
        "scheduled_time": "",
    })
    save_companies(companies)
    print(f"[+] Added {name}")

# ── Web scraping ──────────────────────────────────────────────────────────────

def scrape_page(url, timeout=8):
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=timeout)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser"), r.text
    except Exception:
        return None, None


def find_emails_in_text(text):
    found = re.findall(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text
    )
    noise = {"example", "youremail", "test@", "noreply", "no-reply",
             "sentry.io", "w3.org", "schema.org", "placeholder"}
    return [e for e in found if not any(n in e.lower() for n in noise)]


def get_contact_email(base_url):
    """
    Try common contact/about pages and return the best email found.
    Prefers info@, contact@, hello@, careers@, jobs@ over personal addresses.
    """
    slugs = ["/contact", "/contact-us", "/about", "/about-us",
             "/team", "/careers", "/jobs", ""]
    for slug in slugs:
        url = base_url.rstrip("/") + slug
        _, raw = scrape_page(url)
        if not raw:
            continue
        emails = find_emails_in_text(raw)
        if not emails:
            continue
        priority = [
            e for e in emails
            if re.match(r"(info|contact|hello|careers|jobs|hr|hiring|team)@",
                        e, re.I)
        ]
        return priority[0] if priority else emails[0]
    return None


def get_company_description(base_url):
    """
    Scrape homepage and /about for text to feed into Claude.
    Returns a short string (capped at 600 chars).
    """
    chunks = []
    for slug in ["", "/about", "/about-us"]:
        url = base_url.rstrip("/") + slug
        soup, _ = scrape_page(url)
        if not soup:
            continue
        # meta description is usually the best single sentence
        for attr in [{"name": "description"}, {"property": "og:description"}]:
            tag = soup.find("meta", attrs=attr)
            if tag and tag.get("content", "").strip():
                chunks.append(tag["content"].strip())
        # grab meaningful paragraphs / headings
        for tag in soup.find_all(["h1", "h2", "p"], limit=12):
            t = tag.get_text(" ", strip=True)
            if len(t) > 40:
                chunks.append(t)
        if chunks:
            break
    combined = " | ".join(dict.fromkeys(chunks))  # deduplicate order-preserving
    return combined[:600]

# ── Claude personalization ────────────────────────────────────────────────────

def generate_personalized_line(company_name, description):
    """
    Call Claude to write the sentence fragment that goes after
    'I'm especially interested in {company_name} because of ...'
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    candidate_profile = (
        PROFILE_CACHE_FILE.read_text(encoding="utf-8").strip()
        if PROFILE_CACHE_FILE.exists()
        else "a student seeking a software engineering or AI/ML internship"
    )

    prompt = f"""You are helping a student write a cold internship email.

Candidate background:
{candidate_profile}

Company: {company_name}
What we know about them: {description or "(no description available)"}

Complete this sentence fragment (do NOT include the opening phrase):
  "I'm especially interested in {company_name} because of ___"

Rules:
- One sentence only, under 30 words
- Be specific to what {company_name} actually does — no generic phrases
- End with a period
- Tie it naturally to the candidate's background where relevant
- Output ONLY the fragment, no quotes, no preamble"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )
    if not msg.content:
        raise ValueError("Claude returned an empty response for personalized line.")
    return msg.content[0].text.strip()

# ── Gmail API ─────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, "rb") as f:
                creds = pickle.load(f)
        except Exception:
            print("[*] Stored token is unreadable — re-authenticating...")
            TOKEN_FILE.unlink(missing_ok=True)
            creds = None

    # Re-auth if stored token is missing required scopes
    if creds and not all(s in (creds.scopes or []) for s in GMAIL_SCOPES):
        print("[*] OAuth scopes changed — re-authenticating...")
        creds = None
        TOKEN_FILE.unlink(missing_ok=True)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
            except Exception:
                print("[*] Token refresh failed — re-authenticating...")
                creds = None
                TOKEN_FILE.unlink(missing_ok=True)
        if not creds or not creds.valid:
            if not CREDS_FILE.exists():
                print(
                    f"\n[!] credentials.json not found at {CREDS_FILE}\n"
                    "    See SETUP.md for instructions.\n"
                )
                raise SystemExit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDS_FILE), GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def send_email(service, to, subject, body):
    msg = MIMEMultipart()
    msg["To"]      = to
    msg["From"]    = SENDER_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with open(RESUME_FILE, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    f"attachment; filename={RESUME_FILE.name}")
    msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

def _local_dt_to_rfc3339(dt):
    """Convert a naive local datetime to an RFC 3339 UTC string for the Gmail API."""
    utc_dt = datetime.fromtimestamp(dt.timestamp(), tz=timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def schedule_email_gmail(service, to, subject, body, send_at_dt):
    """Schedule an email using Gmail's native scheduled-send feature."""
    msg = MIMEMultipart()
    msg["To"]      = to
    msg["From"]    = SENDER_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with open(RESUME_FILE, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    f"attachment; filename={RESUME_FILE.name}")
    msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw, "sendAt": _local_dt_to_rfc3339(send_at_dt)},
    ).execute()


# ── Scheduling helpers ────────────────────────────────────────────────────────

SCHEDULE_FMT = "%Y-%m-%d %H:%M"


def _prompt_schedule_time():
    """
    Prompt the user for a send time and return a datetime, or None to cancel.
    Accepted formats:
      YYYY-MM-DD HH:MM      e.g. 2026-04-07 09:00
      today HH:MM           e.g. today 14:30
      tomorrow HH:MM        e.g. tomorrow 9am  /  tomorrow 9:30
    """
    print("\n  When should this email be sent?")
    print("  (e.g. '2026-04-07 09:00', 'today 14:30', 'tomorrow 9am')")
    raw = input("  → ").strip()
    if not raw:
        return None

    # Try strict ISO format first
    for fmt in [SCHEDULE_FMT, "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass

    # Try "today/tomorrow HH[:MM][am/pm]"
    parts = raw.lower().split(None, 1)
    if len(parts) == 2:
        base_str, time_str = parts
        now = datetime.now()
        if base_str == "tomorrow":
            base_date = (now + timedelta(days=1)).date()
        elif base_str == "today":
            base_date = now.date()
        else:
            print("  [!] Unrecognised format. Use: YYYY-MM-DD HH:MM")
            return None

        # Normalise "9am" → "9 AM", "9:30pm" → "9:30 PM", "09:00" stays
        time_str = re.sub(r"([ap])m$", r" \1m", time_str, flags=re.I).upper()
        for time_fmt in ["%I:%M %p", "%I %p", "%H:%M"]:
            try:
                t = datetime.strptime(time_str.strip(), time_fmt).time()
                return datetime.combine(base_date, t)
            except ValueError:
                pass

    print("  [!] Could not parse time. Use format: YYYY-MM-DD HH:MM")
    return None


# ── Interactive review ────────────────────────────────────────────────────────

def review_email(company, body):
    """
    Show draft email to the user.
    Returns (final_body, decision) where decision is 'send', 'skip', or 'schedule'.
    When 'schedule', company['scheduled_time'] is set to the target datetime string.
    """
    print("\n" + "=" * 62)
    print(f"  TO:      {company['contact_email']}")
    print(f"  COMPANY: {company['name']}")
    print("=" * 62)
    print(body)
    print("=" * 62)

    while True:
        choice = input(
            "\n  [s]end   [e]dit line   [d]elay/schedule   [skip]  →  "
        ).strip().lower()

        if choice == "s":
            return body, "send"

        elif choice == "skip":
            return body, "skip"

        elif choice == "d":
            dt = _prompt_schedule_time()
            if dt:
                company["scheduled_time"] = dt.strftime(SCHEDULE_FMT)
                print(f"  Scheduled for {company['scheduled_time']}.")
                return body, "schedule"

        elif choice == "e":
            print("\n  Current line:")
            print(f"  {company['personalized_line']}")
            print("\n  New line (the part after 'because of'): ")
            new_line = input("  → ").strip()
            if not new_line:
                continue
            # Ensure it ends with a period
            if not new_line.endswith("."):
                new_line += "."
            new_body = EMAIL_TEMPLATE.format(
                company_name=company["name"],
                personalized_line=new_line,
            )
            print("\n" + "-" * 62)
            print(new_body)
            print("-" * 62)
            confirm = input("\n  Send this? [y/n]  →  ").strip().lower()
            if confirm == "y":
                company["personalized_line"] = new_line  # persist the edit
                return new_body, "send"
        else:
            print("  Type s, e, d, or skip.")

# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_scheduled_to_gmail(gmail, companies):
    """
    Move any legacy CSV-scheduled entries (status='scheduled') to Gmail's
    native scheduled-send. Updates their status to 'gmail_scheduled' in place.
    """
    legacy = [
        c for c in companies
        if c["status"] == "scheduled" and c.get("scheduled_time")
    ]
    if not legacy:
        return

    print(f"[*] Migrating {len(legacy)} CSV-scheduled email(s) to Gmail scheduled send...")
    changed = False
    for company in legacy:
        try:
            send_at = datetime.strptime(company["scheduled_time"], SCHEDULE_FMT)
        except ValueError:
            print(f"    Skipping {company['name']} — malformed scheduled_time: {company['scheduled_time']!r}")
            continue
        body = EMAIL_TEMPLATE.format(
            company_name=company["name"],
            personalized_line=company["personalized_line"],
        )
        try:
            schedule_email_gmail(
                gmail, company["contact_email"], EMAIL_SUBJECT, body, send_at
            )
            company["status"] = "gmail_scheduled"
            print(f"    Migrated → {company['name']} (scheduled {company['scheduled_time']})")
            changed = True
        except Exception as e:
            print(f"    Failed to migrate {company['name']}: {e} — left as CSV-scheduled")

    if changed:
        save_companies(companies)
    print()


# ── Resume parsing ────────────────────────────────────────────────────────────

def load_resume_profile():
    """
    Return a short text summary of the user's background extracted from Resume.pdf.
    Result is cached in profile_cache.txt so the PDF is only parsed once.
    Delete profile_cache.txt to force a re-parse.
    """
    if PROFILE_CACHE_FILE.exists():
        print("[*] Loading cached resume profile...")
        return PROFILE_CACHE_FILE.read_text(encoding="utf-8")

    if not RESUME_FILE.exists():
        print(f"[!] {RESUME_FILE.name} not found — company suggestions will be less targeted.")
        return ""

    print("[*] Parsing resume (one-time)...")
    text = ""
    try:
        with pdfplumber.open(RESUME_FILE) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
    except Exception as e:
        print(f"[!] Could not open Resume.pdf: {e}")
        return ""

    if not text.strip():
        print("[!] Could not extract text from resume — PDF may be image-based.")
        return ""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": f"""Extract a concise candidate profile from this resume.
Include: key technical skills, notable experience or projects, academic background, and the types of roles being sought.
Write a short paragraph (under 150 words) — it will be used to match the candidate to relevant companies.

Resume:
{text}"""}],
    )
    if not msg.content:
        print("[!] Claude returned an empty response — resume profile not cached.")
        return ""
    profile = msg.content[0].text.strip()
    PROFILE_CACHE_FILE.write_text(profile, encoding="utf-8")
    print("[*] Resume profile cached to profile_cache.txt.")
    return profile


# ── Company discovery ─────────────────────────────────────────────────────────

DISCOVERY_COUNT = 20  # number of companies Claude is asked to suggest


def discover_companies():
    """
    Ask the user for targeting criteria, use Claude to suggest matching companies,
    then review each one-by-one and add approved ones to companies.csv.
    """
    print("\n── Company Discovery ──────────────────────────────────────")

    profile = load_resume_profile()

    print("\n  Answer a few questions to target your search.\n")
    location   = input("  Location/city  (e.g. 'Atlanta, GA'):          ").strip()
    industries = input("  Industries     (e.g. 'fintech, AI, SaaS'):     ").strip()
    size       = input("  Company size   (e.g. 'startup', 'any'):        ").strip()
    extra      = input("  Anything else? (press Enter to skip):          ").strip()

    print("\n[*] Asking Claude for company suggestions...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    profile_section = f"\nCandidate profile:\n{profile}\n" if profile else ""
    extra_section   = f"\n- Additional notes: {extra}" if extra else ""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": f"""You are helping a student find companies to cold email for internships.
{profile_section}
Targeting criteria:
- Location: {location}
- Industries/domains: {industries}
- Company size preference: {size}{extra_section}

Suggest exactly {DISCOVERY_COUNT} companies that are good cold-email targets for this candidate.
Prioritize small-to-mid-size companies (not FAANG-tier) that are more likely to respond to cold outreach.
Only include companies with a real, publicly accessible website.

Return ONLY a valid JSON array with no extra text, in this exact format:
[
  {{"name": "Company Name", "website": "https://www.company.com"}},
  ...
]"""}],
    )

    if not msg.content:
        print("[!] Claude returned an empty response. Try running discovery again.")
        return
    raw = msg.content[0].text.strip()
    # Strip markdown code fences if Claude wrapped the JSON
    raw = re.sub(r"^```[a-z\s]*\n?", "", raw, flags=re.MULTILINE).strip(" `\n")

    try:
        suggestions = json.loads(raw)
    except json.JSONDecodeError:
        # Last-resort: pull the first [...] block out of the response (non-greedy)
        m = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not m:
            print("[!] Could not parse Claude's response. Try running discovery again.")
            return
        try:
            suggestions = json.loads(m.group())
        except json.JSONDecodeError:
            print("[!] Could not parse Claude's response. Try running discovery again.")
            return

    print(f"[*] Got {len(suggestions)} suggestions. Review each one:\n")

    existing       = load_companies()
    existing_names = {c["name"].lower() for c in existing}
    added          = 0

    for i, company in enumerate(suggestions, 1):
        name    = (company.get("name") or "").strip()
        website = (company.get("website") or "").strip()
        if not name or not website:
            continue

        if name.lower() in existing_names:
            print(f"  [{i}/{len(suggestions)}] {name} — already in list, skipping.")
            continue

        print(f"\n  [{i}/{len(suggestions)}] {name}")
        print(f"             {website}")
        choice = input("  [k]eep   [s]kip  →  ").strip().lower()

        if choice == "k":
            existing.append({
                "name":             name,
                "website":          website,
                "contact_email":    "",
                "description":      "",
                "personalized_line":"",
                "status":           "pending",
                "scheduled_time":   "",
            })
            existing_names.add(name.lower())
            added += 1

    if added:
        save_companies(existing)
        print(f"\n[+] Added {added} new company/companies to companies.csv.")
    else:
        print("\n[*] No new companies added.")
    print()


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run():
    # Validate environment
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not SENDER_EMAIL:
        missing.append("GMAIL_SENDER")
    if missing:
        print(f"[!] Missing environment variables: {', '.join(missing)}")
        print("    export ANTHROPIC_API_KEY=sk-...")
        print("    export GMAIL_SENDER=you@gmail.com")
        raise SystemExit(1)

    if not RESUME_FILE.exists():
        print(f"[!] {RESUME_FILE.name} not found in {BASE_DIR}")
        print("    Place your resume in the project folder named 'Resume.pdf'.")
        raise SystemExit(1)

    if any(p in EMAIL_TEMPLATE for p in ("[YOUR", "[e.g.", "[Customize")) or "[Customize" in EMAIL_SUBJECT:
        print("[!] It looks like you haven't filled in the email template yet.")
        print("    Edit YOUR_NAME, YOUR_CONTEXT, YOUR_RESEARCH, YOUR_ROLES, and")
        print("    EMAIL_SUBJECT at the top of email_pipeline.py before running.")
        raise SystemExit(1)

    print("\n── Email Automation Pipeline ──────────────────────────────")

    # Determine auto-send mode — env var can force it on, otherwise prompt
    auto_send = AUTO_SEND
    if not auto_send:
        ans = input(
            "  Enable auto-send? Emails will be sent without review. [y/N]  →  "
        ).strip().lower()
        auto_send = ans == "y"

    if auto_send:
        print("[!] AUTO-SEND is ON — emails will be sent without review.")
    else:
        print("[*] Review mode — you will approve each email before it sends.")

    # ── Discovery prompt ───────────────────────────────────────────────────
    ans = input(
        "  Discover new companies first? Claude will suggest targets based on your resume. [y/N]  →  "
    ).strip().lower()
    if ans == "y":
        discover_companies()

    print("[*] Loading company list...")
    companies = seed_csv()

    print("[*] Connecting to Gmail (browser will open on first run)...")
    gmail = get_gmail_service()

    # ── Migrate any CSV-scheduled emails to Gmail native scheduling ────────
    migrate_scheduled_to_gmail(gmail, companies)

    pending = [c for c in companies if c["status"] == "pending"]
    total   = len(pending)
    scheduled_count = sum(1 for c in companies if c["status"] in ("scheduled", "gmail_scheduled"))
    print(f"[*] {total} pending  |  {scheduled_count} scheduled.\n")

    if total == 0:
        print("[*] Nothing new to process.")
        if scheduled_count:
            print(f"[*] {scheduled_count} email(s) are scheduled for later — run again to send them.")
        return

    for idx, company in enumerate(companies, 1):
        if company["status"] != "pending":
            continue

        print(f"[{idx}/{len(companies)}] {company['name']}")

        # ── 1. Find contact email ──────────────────────────────────────────
        if not company["contact_email"]:
            print(f"        Scraping contact page...")
            email = get_contact_email(company["website"])
            if not email:
                print(f"        No email found — skipping.\n")
                company["status"] = "skip"
                save_companies(companies)
                continue
            company["contact_email"] = email
            print(f"        Email: {email}")

        # ── 2. Get description ─────────────────────────────────────────────
        if not company["description"]:
            print(f"        Scraping company description...")
            company["description"] = get_company_description(company["website"])

        # ── 3. Generate personalized line ──────────────────────────────────
        if not company["personalized_line"]:
            print(f"        Generating personalized line via Claude...")
            try:
                line = generate_personalized_line(
                    company["name"], company["description"]
                )
                company["personalized_line"] = line
                print(f"        Line: {line}")
            except Exception as e:
                print(f"        Claude error: {e} — skipping.\n")
                company["status"] = "skip"
                save_companies(companies)
                continue

        save_companies(companies)

        # ── 4. Build email ─────────────────────────────────────────────────
        body = EMAIL_TEMPLATE.format(
            company_name=company["name"],
            personalized_line=company["personalized_line"],
        )

        # ── 5. Review and send ─────────────────────────────────────────────
        try:
            if auto_send:
                print(f"        Auto-send — skipping review.")
                decision = "send"
            else:
                body, decision = review_email(company, body)
            if decision == "send":
                send_email(gmail, company["contact_email"], EMAIL_SUBJECT, body)
                company["status"] = "sent"
                save_companies(companies)
                print(f"        Sent.\n")
            elif decision == "schedule":
                send_at = datetime.strptime(company["scheduled_time"], SCHEDULE_FMT)
                try:
                    schedule_email_gmail(
                        gmail, company["contact_email"], EMAIL_SUBJECT, body, send_at
                    )
                    company["status"] = "gmail_scheduled"
                    save_companies(companies)
                    print(f"        Scheduled in Gmail for {company['scheduled_time']}.\n")
                except Exception as e:
                    print(f"        Gmail scheduling failed: {e}")
                    print(f"        Falling back to CSV-based scheduling.\n")
                    company["status"] = "scheduled"
                    save_companies(companies)
            else:
                company["status"] = "skip"
                save_companies(companies)
                print(f"        Skipped.\n")

        except Exception as e:
            print(f"        Send failed: {e}\n")
            # Don't mark as sent — will retry on next run
            save_companies(companies)

        time.sleep(1.5)  # brief pause between requests

    print("── Done ───────────────────────────────────────────────────")
    sent      = sum(1 for c in companies if c["status"] == "sent")
    skipped   = sum(1 for c in companies if c["status"] == "skip")
    scheduled = sum(1 for c in companies if c["status"] in ("scheduled", "gmail_scheduled"))
    print(f"    Sent: {sent}  |  Skipped: {skipped}  |  Scheduled: {scheduled}")
    if scheduled:
        print("\n    Upcoming scheduled sends:")
        for c in sorted(
            (c for c in companies if c["status"] in ("scheduled", "gmail_scheduled")),
            key=lambda x: x["scheduled_time"],
        ):
            tag = "(Gmail)" if c["status"] == "gmail_scheduled" else "(local)"
            print(f"      {c['scheduled_time']}  →  {c['name']} <{c['contact_email']}> {tag}")
    print(f"\n    Full log: {COMPANIES_FILE}\n")


if __name__ == "__main__":
    run()
