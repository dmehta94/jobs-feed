"""Email a short list of this week's new postings after each collection run.

Sends through Gmail SMTP using an app password. Skipped quietly when the
secrets aren't set, so the collector works without it.

Environment (set as GitHub repository secrets/variables, never committed):
    GMAIL_ADDRESS       the Gmail account that sends the email
    GMAIL_APP_PASSWORD  a Gmail app password for that account
    DIGEST_TO           recipient (defaults to GMAIL_ADDRESS)
    MIN_SALARY          optional: drop postings whose listed top of range is below this
    EXCLUDE_TERMS       optional: comma-separated terms; postings mentioning any are dropped
    MAX_YEARS           optional: drop postings asking for at least this many years (default 5)

Run from the repo root:  python -m collector.email_digest
"""
import html
import json
import os
import smtplib
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MAX_LISTED = 40


def _env_int(name):
    try:
        return int(os.environ.get(name, "").strip())
    except ValueError:
        return None


def select(feed, today=None):
    """New postings that pass the optional filters, best-first."""
    today = today or date.today()
    min_salary = _env_int("MIN_SALARY")
    max_years = _env_int("MAX_YEARS") or 5
    terms = [t.strip().lower() for t in os.environ.get("EXCLUDE_TERMS", "").split(",") if t.strip()]
    recent = (today - timedelta(days=14)).isoformat()

    picked = []
    for job in feed.get("jobs", []):
        if feed.get("first_run"):
            if job.get("posted") and job["posted"] < recent:
                continue
        elif not job.get("new_this_week"):
            continue
        if job.get("phd_required"):
            continue
        if job.get("years_required") and job["years_required"] >= max_years:
            continue
        if min_salary and job.get("salary_max") and job["salary_max"] < min_salary:
            continue
        haystack = " ".join(str(job.get(k) or "") for k in ("org", "title", "description")).lower()
        if any(t in haystack for t in terms):
            continue
        picked.append(job)
    picked.sort(key=lambda j: (j.get("tier", 9), j.get("org", ""), j.get("title", "")))
    return picked


def _pay(job):
    if job.get("salary_min") and job.get("salary_max"):
        lo, hi = int(job["salary_min"]), int(job["salary_max"])
        return f"${lo:,}" if lo == hi else f"${lo:,}–${hi:,}"
    return "pay not listed"


def _where(job):
    loc = job.get("location") or ""
    if job.get("remote") and "remote" not in loc.lower():
        loc = f"{loc} (remote)" if loc else "Remote"
    return loc or "location not listed"


def _notes(job):
    bits = []
    if job.get("years_required"):
        bits.append(f"{job['years_required']}+ yrs asked")
    if job.get("exam_required"):
        bits.append("civil service exam")
    if job.get("post_until"):
        bits.append(f"closes {job['post_until']}")
    return " · ".join(bits)


GROUPS = [
    ("Tier 1 organizations", lambda j: j.get("tier") == 1),
    ("Other target organizations", lambda j: j.get("tier") == 2 and j.get("category") != "NYC Government"),
    ("NYC government", lambda j: j.get("category") == "NYC Government"),
    ("Federal", lambda j: j.get("category") == "Federal Government"),
    ("Found by keyword search", lambda j: j.get("category") == "Discovered"),
]


def build(feed, jobs, health, today=None):
    today = today or date.today()
    subject = f"Job feed — {today.strftime('%b')} {today.day}: {len(jobs)} new posting{'s' if len(jobs) != 1 else ''}"
    broken = [s["org"] for s in health.get("sources", []) if s.get("tier") == 1 and s.get("status") != "ok"]
    footer = f"{feed.get('sources_ok', 0)} of {feed.get('sources_total', 0)} sources working."
    if broken:
        footer += " Not reading: " + ", ".join(broken) + "."
    if feed.get("first_run"):
        footer += " First run: showing postings from the last two weeks."

    text_lines, html_parts = [subject, ""], [f"<h2 style='font-family:sans-serif'>{html.escape(subject)}</h2>"]
    shown = 0
    for heading, test in GROUPS:
        group = [j for j in jobs if test(j)]
        if not group:
            continue
        text_lines.append(heading.upper())
        html_parts.append(f"<h3 style='font-family:sans-serif;margin-bottom:4px'>{html.escape(heading)}</h3><ul style='font-family:sans-serif;padding-left:18px'>")
        for job in group:
            if shown >= MAX_LISTED:
                break
            shown += 1
            notes = _notes(job)
            line = f"{job['org']} — {job['title']} · {_pay(job)} · {_where(job)}"
            text_lines.append(f"- {line}{' · ' + notes if notes else ''}\n  {job.get('url')}")
            html_parts.append(
                f"<li style='margin-bottom:6px'><b>{html.escape(job['org'])}</b> — "
                f"<a href='{html.escape(job.get('url') or '', quote=True)}'>{html.escape(job['title'])}</a>"
                f"<br><span style='color:#555'>{html.escape(_pay(job))} · {html.escape(_where(job))}"
                f"{' · ' + html.escape(notes) if notes else ''}</span></li>")
        html_parts.append("</ul>")
        text_lines.append("")
    if not jobs:
        text_lines.append("Nothing new this week.")
        html_parts.append("<p style='font-family:sans-serif'>Nothing new this week.</p>")
    if len(jobs) > MAX_LISTED:
        more = f"…and {len(jobs) - MAX_LISTED} more in data/jobs.json."
        text_lines.append(more)
        html_parts.append(f"<p style='font-family:sans-serif'>{html.escape(more)}</p>")
    text_lines += ["", footer, "The ranked digest arrives in the Claude app at 7:19 a.m."]
    html_parts.append(f"<p style='font-family:sans-serif;color:#777;font-size:12px'>{html.escape(footer)}<br>"
                      "The ranked digest arrives in the Claude app at 7:19 a.m.</p>")
    return subject, "\n".join(text_lines), "".join(html_parts)


def send(subject, text, html_body):
    sender, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("DIGEST_TO") or sender
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, [to], msg.as_string())


def main():
    if not os.environ.get("GMAIL_ADDRESS") or not os.environ.get("GMAIL_APP_PASSWORD"):
        print("Email skipped: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set.")
        return 0
    feed = json.loads((DATA / "jobs.json").read_text())
    health = json.loads((DATA / "health.json").read_text())
    jobs = select(feed)
    subject, text, html_body = build(feed, jobs, health)
    send(subject, text, html_body)
    print(f"Emailed {len(jobs)} postings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
