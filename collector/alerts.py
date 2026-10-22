"""Postings from the owner's LinkedIn job-alert emails, read privately for the weekly email.

LinkedIn finds roles at employers the collector doesn't know about, and its alert emails are the
one place those reach us. They're read over IMAP with the same Gmail app password the email step
already uses, read-only (messages aren't marked as read).

Privacy: this repository is public, so nothing read here is written to data/, committed, or printed
to the Actions log beyond a count. The postings go only into the email. Alert emails carry
LinkedIn sign-in tokens in their links, so links are rebuilt as plain
https://www.linkedin.com/jobs/view/<id>/ addresses and nothing else from the URLs is kept.
"""
import csv
import email
import imaplib
import os
import re
from datetime import date, timedelta
from email.policy import default as default_policy
from pathlib import Path

from .discovery import has_mission
from .fit import score
from .text import NON_US, US_MARKER, extract_salary, html_to_text, location_is_ok, title_is_relevant

ROOT = Path(__file__).resolve().parent.parent
SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com")
MAX_MESSAGES = 400

_VIEW = re.compile(r"View job:\s*https://www\.linkedin\.com/(?:comm/)?jobs/view/(\d+)\S*")
_NOISE = re.compile(r"^(this company is actively hiring|actively recruiting|easy apply|promoted|be an early applicant|"
                    r"\d+ (?:school|company) alumni.*|.*connections? work here.*|.*applicants?|new|top applicant|"
                    r"you(?:'re| are) a top applicant.*|-{3,})$", re.I)
_REMOTE = re.compile(r"\b(remote|telework)\b", re.I)
_GENERIC = {"university", "college", "school", "of", "the", "and", "for", "graduate", "education", "inc", "llc",
            "institute", "center", "foundation", "at", "us", "international", "group", "nyc", "new", "york"}


def parse_alert(text):
    """Title, employer, and location for each posting in one alert email's plain text."""
    jobs, start = [], 0
    for match in _VIEW.finditer(text or ""):
        block = text[start:match.start()]
        start = match.end()
        lines = [line.strip() for line in block.splitlines()]
        # The posting's lines follow the last line holding a link (the header or the previous posting).
        last_link = max((i for i, line in enumerate(lines) if "http" in line), default=-1)
        lines = [line for line in lines[last_link + 1:] if line and not _NOISE.match(line)]
        if len(lines) < 2:
            continue
        title, org = lines[0], lines[1]
        location = lines[2] if len(lines) > 2 and not lines[2].startswith("$") else ""
        smin, smax, stext = extract_salary(" ".join(lines[2:]))
        jobs.append({"id": match.group(1), "title": title, "org": org, "location": location,
                     "remote": bool(_REMOTE.search(location + " " + title)) or None,
                     "url": f"https://www.linkedin.com/jobs/view/{match.group(1)}/",
                     "salary_min": smin, "salary_max": smax, "salary_text": stext,
                     "category": "LinkedIn alert", "tier": 3, "description": ""})
    return jobs


def _plain_text(raw_bytes):
    msg = email.message_from_bytes(raw_bytes, policy=default_policy)
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    body = part.get_content()
    return body if part.get_content_type() == "text/plain" else html_to_text(body)


def read_mailbox(days=8):
    """Plain-text bodies of recent LinkedIn alert emails. Empty when the Gmail secrets aren't set."""
    address, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not password:
        return []
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    bodies = []
    imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=60)
    try:
        imap.login(address, password)
        status, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
        if status != "OK":
            imap.select("INBOX", readonly=True)
        ids = set()
        for sender in SENDERS:
            status, data = imap.search(None, "FROM", f'"{sender}"', "SINCE", since)
            if status == "OK" and data and data[0]:
                ids.update(data[0].split())
        for msg_id in sorted(ids, key=int)[-MAX_MESSAGES:]:
            status, data = imap.fetch(msg_id, "(BODY.PEEK[])")
            for item in data or []:
                if isinstance(item, tuple):
                    bodies.append(_plain_text(item[1]))
    finally:
        try:
            imap.logout()
        except Exception:  # already disconnected
            pass
    return bodies


def _target_words():
    try:
        with open(ROOT / "orgs.csv", newline="", encoding="utf-8") as f:
            names = [row["name"] for row in csv.DictReader(f)]
    except OSError:
        names = []
    return [_words(n) for n in names if _words(n)]


def _words(name):
    return set(re.findall(r"[a-z]+", (name or "").lower())) - _GENERIC


def _place_plausible(location, remote):
    """LinkedIn often shows a bare city ("Cambridge"), so only drop clearly out-of-area or non-US places."""
    loc = (location or "").strip()
    if loc and NON_US.search(loc) and not US_MARKER.search(loc):
        return False
    if not loc or remote or location_is_ok(loc, remote):
        return True
    return "," not in loc  # "Cambridge" is ambiguous; "Reston, VA" is clearly elsewhere


def pick_alerts(bodies, feed_jobs, max_items=8, min_score=4):
    """Relevant, mission-fit alert postings not already in the collected feed, best first."""
    targets = _target_words()
    known = {}
    for job in feed_jobs:
        known.setdefault(re.sub(r"[^a-z0-9]", "", (job.get("title") or "").lower()), []).append(_words(job.get("org")))
    seen, scored = set(), []
    for body in bodies:
        for job in parse_alert(body):
            if job["id"] in seen:
                continue
            seen.add(job["id"])
            if not title_is_relevant(job["title"]) or not _place_plausible(job["location"], job["remote"]):
                continue
            org_words = _words(job["org"])
            is_target = any(org_words & t for t in targets)
            if not (is_target or has_mission(job)):
                continue
            title_key = re.sub(r"[^a-z0-9]", "", job["title"].lower())
            if any(org_words & w for w in known.get(title_key, [])):
                continue  # already in the main list, with full details
            points, why = score({**job, "category": "Research & Policy" if is_target else "LinkedIn alert"})
            if points is None or points < min_score:
                continue
            scored.append((points, job, why))
    scored.sort(key=lambda t: -t[0])
    return [{**job, "fit_score": points, "fit_reasons": why} for points, job, why in scored[:max_items]]
