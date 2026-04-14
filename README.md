# Cold Email Automation Pipeline

Automates personalized cold outreach emails using Claude AI and the Gmail API.

For each company, the pipeline:
1. Scrapes the company website to find a contact email and description
2. Uses Claude to generate a personalized sentence specific to that company
3. Builds a complete email from your template
4. Lets you review, edit, schedule, or skip before sending (or auto-send if enabled)

## Requirements

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/)
- A Gmail account with OAuth credentials (see setup below)

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set environment variables
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GMAIL_SENDER=yourname@gmail.com
```
Add these to your `~/.zshrc` or `~/.bashrc` to make them permanent.

### 3. Add your resume
Place your resume in the project folder named `Resume.pdf`.

### 4. Get Gmail credentials (one-time)
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Go to **APIs & Services → Library**, search for **Gmail API**, and enable it
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, fill in an app name (e.g. "Email Pipeline")
   - Add your Gmail address as a test user
5. Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON file
6. Rename the downloaded file to `credentials.json` and place it in this folder

### 5. Customize the email template
At the top of `email_pipeline.py`, fill in your personal details:
```python
YOUR_NAME     = "Your Name"
YOUR_CONTEXT  = "a sophomore at X University studying CS"
YOUR_RESEARCH = "I recently completed an internship at Y doing Z"
YOUR_ROLES    = "software engineering, AI/ML, or data science"
EMAIL_SUBJECT = "Your subject line here"
```

### 6. Add target companies
The easiest way is to use the built-in discovery feature (see below). You can also edit the `SEED_COMPANIES` list in `email_pipeline.py` or add rows directly to `companies.csv`. See `companies_example.csv` for the expected format.

## Running

```bash
python email_pipeline.py
```

On the first run a browser window will open to authorize Gmail access. This only happens once — the token is saved locally.

You will be asked two questions at startup:

**1. Enable auto-send?**
- **No (default):** each email is shown for review before sending
- **Yes:** all emails are sent automatically without review

**2. Discover new companies?**
If yes, Claude will suggest ~20 companies based on your resume and your answers to a few targeting questions (location, industry, size). You review each suggestion one by one and choose which to add. Your resume is parsed once and cached — delete `profile_cache.txt` to re-parse after updating your resume.

### Review mode options
```
[s]end   [e]dit line   [d]elay/schedule   [skip]
```
- `s` — send as-is
- `e` — manually edit the personalized line, then send
- `d` — schedule via Gmail's native scheduled send
- `skip` — skip this company

## File overview

| File | Description |
|------|-------------|
| `email_pipeline.py` | Main script |
| `requirements.txt` | Python dependencies |
| `companies_example.csv` | Example CSV format |
| `SETUP.md` | Detailed setup reference |

The following files are gitignored and must be created locally:

| File | Description |
|------|-------------|
| `credentials.json` | Google OAuth client secret (downloaded from Google Cloud) |
| `token.pickle` | OAuth token (auto-generated on first run) |
| `Resume.pdf` | Your resume, attached to every email |
| `companies.csv` | Your outreach tracker (auto-generated on first run) |
| `profile_cache.txt` | Cached resume profile (auto-generated on first discovery run) |
