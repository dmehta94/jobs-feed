"""Email a short list of this week's new postings after each collection run.

Sends through Gmail SMTP using an app password. Skipped quietly when the
secrets aren't set, so the collector works without it.

Environment (set as GitHub repository secrets/variables, never committed):
    GMAIL_ADDRESS       the Gmail account that sends the email
    GMAIL_APP_PASSWORD  a Gmail app password for that account
    DIGEST_TO           recipient (defaults to GMAIL_ADDRESS)
    MIN_SALARY          optional: drop postings whose listed top of range is below this
    EXCLUDE_TERMS       optional: comma-separated terms; postings mentioning any are dropped
    EXCLUDE_EMPLOYERS   optional: comma-separated employer names, matched against the employer only;
                        prefix a name with ! to exempt it (e.g. "Google, !DeepMind" keeps Google DeepMind)
    MAX_YEARS           optional: drop postings asking for at least this many years (default 5)
    MAX_ITEMS           optional: most postings to list (default 15)
    MAX_PER_ORG         optional: most postings from one employer (default 3)
    MAX_NYC             optional: most NYC city postings (default 5)
    MIN_FIT             optional: lowest fit score to include (default 5; higher means stricter)
    READ_LINKEDIN_ALERTS optional: "no" turns off the LinkedIn alerts section (see alerts.py)
    DRY_RUN             set by a manual run with email=no: build the email but don't send it

Run from the repo root:  python -m collector.email_digest
"""
import html
import json
import os
import re
import smtplib
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from . import alerts as linkedin_alerts
from .fit import shortlist

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


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
    names = [t.strip().lower() for t in os.environ.get("EXCLUDE_EMPLOYERS", "").split(",") if t.strip()]
    employers = [n for n in names if not n.startswith("!")]
    exempt = [n[1:].strip() for n in names if n.startswith("!")]
    recent = (today - timedelta(days=14)).isoformat()

    picked = []
    for job in feed.get("jobs", []):
        if feed.get("first_run"):
            if job.get("posted") and job["posted"] < recent:
                continue
        elif not job.get("new_this_week"):
            continue
        if job.get("advanced_degree_required") or job.get("phd_required"):
            continue
        if job.get("years_required") and job["years_required"] >= max_years:
            continue
        if min_salary and job.get("salary_max") and job["salary_max"] < min_salary:
            continue
        haystack = " ".join(str(job.get(k) or "") for k in ("org", "title", "description")).lower()
        if any(t in haystack for t in terms):
            continue
        org = (job.get("org") or "").lower()
        if any(e in org for e in employers) and not any(x and x in org for x in exempt):
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


APPLY_FIRST = 3      # top-ranked postings shown with their reasons
WORTH_A_LOOK = 7     # the next ones, one line each; anything ranked lower is listed under "Also new"
CLOSING_SOON_DAYS = 14
REASON_TEXT = {"data/research role": "data or research role", "curriculum role": "curriculum role",
               "education focus": "education focus", "entry-level": "entry-level title"}


def _title(job):
    return re.sub(r"^Job Title\s+", "", job.get("title") or "")  # some iCIMS boards prefix titles with this


def _closes(job, today):
    """('Nov 15', days left) for a posting with a known closing date, else (None, None)."""
    try:
        closes = date.fromisoformat(job.get("post_until") or "")
    except ValueError:
        return None, None
    return f"{closes.strftime('%b')} {closes.day}", (closes - today).days


def _why(job):
    bits = [REASON_TEXT[r] for r in job.get("fit_reasons", []) if r in REASON_TEXT]
    years = job.get("years_required")
    if years is not None:
        bits.append(f"asks {years} yr{'s' if years != 1 else ''}")
    if job.get("tier") == 1:
        bits.append("tier 1 organization")
    elif job.get("category") == "Discovered":
        bits.append("found by keyword search")
    if job.get("exam_required"):
        bits.append("civil service exam required")
    return bits


def _item(job, today, detailed):
    """One posting as (text, html). Detailed items get a 'Why' line."""
    title, pay, where = _title(job), _pay(job), _where(job)
    closes, days = _closes(job, today)
    deadline = f"closes {closes}" if closes else ""
    urgent = closes and days is not None and 0 <= days <= CLOSING_SOON_DAYS
    why = " · ".join(_why(job))
    text = f"- {job['org']} — {title}\n  {pay} · {where}" + (f" · {deadline.upper() if urgent else deadline}" if closes else "")
    if detailed and why:
        text += f"\n  Why: {why}"
    text += f"\n  {job.get('url')}"
    deadline_html = ""
    if closes:
        style = "color:#b00020;font-weight:bold" if urgent else "color:#555"
        deadline_html = f" · <span style='{style}'>{html.escape(deadline)}</span>"
    why_html = (f"<br><span style='color:#333'>Why: {html.escape(why)}</span>" if detailed and why else "")
    li = (f"<li style='margin-bottom:{10 if detailed else 6}px'><b>{html.escape(job['org'])}</b> — "
          f"<a href='{html.escape(job.get('url') or '', quote=True)}'>{html.escape(title)}</a>"
          f"<br><span style='color:#555'>{html.escape(pay)} · {html.escape(where)}</span>{deadline_html}{why_html}</li>")
    return text, li


def build(feed, jobs, health, today=None, matched=None, alerts=None):
    """jobs arrive best-first (fit.shortlist). The email keeps that order: the top few with reasons,
    then the next several, then the rest."""
    today = today or date.today()
    first, rest = jobs[:APPLY_FIRST], jobs[APPLY_FIRST:]
    look, also = rest[:WORTH_A_LOOK], rest[WORTH_A_LOOK:]
    if jobs:
        subject = f"Job feed — {today.strftime('%b')} {today.day}: {len(first)} to apply to first"
        if rest:
            subject += f", {len(rest)} more new"
    else:
        subject = f"Job feed — {today.strftime('%b')} {today.day}: nothing new"
    if alerts:
        subject += f" + {len(alerts)} from LinkedIn"
    broken = [s["org"] for s in health.get("sources", []) if s.get("tier") == 1 and s.get("status") != "ok"]
    footer = f"{feed.get('sources_ok', 0)} of {feed.get('sources_total', 0)} sources working."
    if broken:
        footer += " Not reading: " + ", ".join(broken) + "."
    if feed.get("first_run"):
        footer += " First run: showing postings from the last two weeks."

    text_lines, html_parts = [subject, ""], [f"<h2 style='font-family:sans-serif'>{html.escape(subject)}</h2>"]
    soon = []
    for job in jobs:
        closes, days = _closes(job, today)
        if closes and 0 <= days <= CLOSING_SOON_DAYS:
            soon.append(f"{job['org']} — {_title(job)} (closes {closes})")
    if soon:
        text_lines += ["CLOSING WITHIN TWO WEEKS: " + "; ".join(soon), ""]
        html_parts.append("<p style='font-family:sans-serif;color:#b00020'><b>Closing within two weeks:</b> "
                          + html.escape("; ".join(soon)) + "</p>")
    sections = [("Apply first", "Best matches this week, highest first.", first, True),
                ("Worth a look", "", look, False),
                ("Also new", "Lower matches; skim if you have time.", also, False)]
    for heading, note, group, detailed in sections:
        if not group:
            continue
        text_lines.append(heading.upper() + (f" — {note}" if note else ""))
        html_parts.append(f"<h3 style='font-family:sans-serif;margin-bottom:2px'>{html.escape(heading)}</h3>"
                          + (f"<p style='font-family:sans-serif;color:#555;margin-top:0'>{html.escape(note)}</p>" if note else "")
                          + "<ul style='font-family:sans-serif;padding-left:18px'>")
        for job in group:
            text, li = _item(job, today, detailed)
            text_lines.append(text)
            html_parts.append(li)
        html_parts.append("</ul>")
        text_lines.append("")
    if alerts:
        heading = "From your LinkedIn alerts"
        note = "Employers outside the collector's list. Requirements weren't checked; open the posting to see them."
        text_lines += [heading.upper(), note]
        html_parts.append(f"<h3 style='font-family:sans-serif;margin-bottom:4px'>{html.escape(heading)}</h3>"
                          f"<p style='font-family:sans-serif;color:#555;margin-top:0'>{html.escape(note)}</p>"
                          "<ul style='font-family:sans-serif;padding-left:18px'>")
        for job in alerts:
            pay = _pay(job) if job.get("salary_max") else "pay not shown"
            text_lines.append(f"- {job['org']} — {job['title']} · {pay} · {_where(job)}\n  {job['url']}")
            html_parts.append(
                f"<li style='margin-bottom:6px'><b>{html.escape(job['org'])}</b> — "
                f"<a href='{html.escape(job['url'], quote=True)}'>{html.escape(job['title'])}</a>"
                f"<br><span style='color:#555'>{html.escape(pay)} · {html.escape(_where(job))}</span></li>")
        html_parts.append("</ul>")
        text_lines.append("")
    if not jobs and not alerts:
        text_lines.append("Nothing new this week.")
        html_parts.append("<p style='font-family:sans-serif'>Nothing new this week.</p>")
    if matched and matched > len(jobs):
        more = f"{matched - len(jobs)} lower-ranked matches left out. The Claude digest reads all of them."
        text_lines.append(more)
        html_parts.append(f"<p style='font-family:sans-serif;color:#555'>{html.escape(more)}</p>")
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


def pick(feed):
    """Filtered, ranked, and capped list for the email. Returns (jobs, number that matched)."""
    return shortlist(select(feed),
                     max_items=_env_int("MAX_ITEMS") or 15,
                     max_per_org=_env_int("MAX_PER_ORG") or 3,
                     max_nyc=_env_int("MAX_NYC") if _env_int("MAX_NYC") is not None else 5,
                     min_score=_env_int("MIN_FIT") if _env_int("MIN_FIT") is not None else 5)


def main():
    if not os.environ.get("GMAIL_ADDRESS") or not os.environ.get("GMAIL_APP_PASSWORD"):
        print("Email skipped: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set.")
        return 0
    feed = json.loads((DATA / "jobs.json").read_text())
    health = json.loads((DATA / "health.json").read_text())
    jobs, matched = pick(feed)
    alerts, alert_status, emails_read = [], "off", 0
    if os.environ.get("READ_LINKEDIN_ALERTS", "yes").lower() != "no":
        try:
            bodies = linkedin_alerts.read_mailbox()
            emails_read = len(bodies)
            alerts = linkedin_alerts.pick_alerts(bodies, feed.get("jobs", []))
            alert_status = "ok"
        except Exception as exc:  # the log is public: report the kind of failure, never mailbox content
            alert_status = f"failed ({type(exc).__name__})"
            print(f"LinkedIn alerts skipped ({type(exc).__name__}).")
    # Counts only, so the public repo shows whether the email step worked without revealing what it read.
    # last_sent survives dry runs; the collector uses it to decide which postings count as new.
    status_path = DATA / "email_status.json"
    try:
        last_sent = json.loads(status_path.read_text()).get("last_sent")
    except (FileNotFoundError, ValueError):
        last_sent = None
    status = {
        "date": date.today().isoformat(), "dry_run": bool(os.environ.get("DRY_RUN")), "postings": len(jobs),
        "matched": matched, "linkedin_alerts": alert_status, "alert_emails_read": emails_read,
        "alert_postings": len(alerts), "last_sent": last_sent}
    status_path.write_text(json.dumps(status, indent=2) + "\n")
    subject, text, html_body = build(feed, jobs, health, matched=matched, alerts=alerts)
    if os.environ.get("DRY_RUN"):
        print(f"Dry run: would email {len(jobs)} postings and {len(alerts)} from LinkedIn alerts.")
        return 0
    send(subject, text, html_body)
    status["last_sent"] = date.today().isoformat()
    status_path.write_text(json.dumps(status, indent=2) + "\n")
    print(f"Emailed {len(jobs)} postings and {len(alerts)} from LinkedIn alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
