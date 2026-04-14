"""
reset_pending.py
Resets all companies that have a contact email back to 'pending' status.
Useful for re-running the pipeline after updating your email template.

Usage: python reset_pending.py
"""
import csv
from pathlib import Path

f = Path(__file__).parent / "companies.csv"

if not f.exists():
    print("[!] companies.csv not found.")
    raise SystemExit(1)

with open(f, newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))

if not rows:
    print("[!] companies.csv is empty — nothing to reset.")
    raise SystemExit(0)

for r in rows:
    if r["contact_email"]:
        r["status"] = "pending"

with open(f, "w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"Reset {sum(1 for r in rows if r['status'] == 'pending')} companies to pending.")
