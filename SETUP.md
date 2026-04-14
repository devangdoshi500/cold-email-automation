# Setup Guide

## 1. Install dependencies
```
cd Email_Automation
pip install -r requirements.txt
```

## 2. Set environment variables
```
export ANTHROPIC_API_KEY=sk-ant-...
export GMAIL_SENDER=yourname@gmail.com
```
Add these to your `~/.zshrc` to make them permanent.

## 3. Get Gmail credentials (one-time)

1. Go to https://console.cloud.google.com
2. Create a new project (or use an existing one)
3. Go to **APIs & Services → Library**, search for **Gmail API**, enable it
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, fill in app name (anything, e.g. "Email Pipeline")
   - Add your Gmail address as a test user
5. Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON file
6. Rename the downloaded file to `credentials.json` and place it in this folder

## 4. Run
```
python email_pipeline.py
```
On the first run, a browser window will open asking you to authorize the app
with your Google account. Click through — this only happens once.
Token is saved to `token.pickle` for future runs.

## 5. Review mode (default)
Each email is shown for your approval before sending:
- `s`    → send as-is
- `e`    → edit the personalized line, then send
- `d`    → delay/schedule via Gmail's native scheduled send
- `skip` → skip this company

## 6. Auto-send mode (optional)
To skip the review prompt and send all emails automatically:
```
export AUTO_SEND=true
python email_pipeline.py
```
Or set `AUTO_SEND = True` directly at the top of `email_pipeline.py`.

## 7. Company discovery
At startup you will be asked if you want to discover new companies. If yes:
- Your resume (`Resume.pdf`) is parsed by Claude once and cached to `profile_cache.txt`
- You answer three questions: location, industries, and company size preference
- Claude suggests ~20 matching companies; you review each one and choose to keep or skip
- Kept companies are added to `companies.csv` as `pending` and processed in the same run

To re-parse your resume (e.g. after updating it), delete `profile_cache.txt`.

## 8. Adding companies manually
Edit `companies.csv` directly, or add to the `SEED_COMPANIES` list in
`email_pipeline.py` and re-run — new entries are appended automatically.
Companies already marked `sent` or `skip` are never re-processed.
