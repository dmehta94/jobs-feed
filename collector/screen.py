"""Daily screen: the new postings worth a closer read, and a short log of what was dropped and why.

Reads data/jobs.json and writes data/daily_candidates.json. A posting is in the window when it was first
seen in the past WINDOW_DAYS days; a reader that keeps its own last-run time takes only the postings with
first_seen_at after it, so a missed day is picked up on the next one instead of lost.

Only clear cases are dropped here: a rule fires when the posting's own fields say so plainly. Anything
ambiguous passes with a flag, and judging fit (commute, track, day-to-day work) is left to the reader.

Run from the repo root:  python -m collector.screen
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fit import CHARTER, FORCE_ORG, FORCE_TITLE
from .text import ANYWHERE, DC_AREA, NYC_AREA, _HYBRID, _NOT_REMOTE, _SPLIT

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WINDOW_DAYS = 7
TRIM_CHARS = 3500
SUMMARY_CHARS = 600
PAY_FLOOR = 60000        # bridge track floor; nothing below it can pass either track
DS_PAY_FLOOR = 80000     # data science track floor; between the two, the role can only be a bridge role
MAX_YEARS = 5

# Frontier AI labs and big-tech AI employers. Anthropic and Google DeepMind are the exceptions; DeepMind
# roles still need reading (Gemini and product roles are out), which the reader does.
AI_EMPLOYER = re.compile(r"^\W*(openai|google(?!\s*deepmind)|alphabet|meta(?: platforms)?|facebook|microsoft|amazon|aws|"
                         r"x\.?ai|mistral(?: ai)?|cohere)\b", re.I)
DEFENSE_CONTRACTOR = re.compile(
    r"\b(lockheed|raytheon|\brtx\b|northrop|general dynamics|bae systems|l3harris|leidos|booz allen|\bsaic\b|"
    r"science applications international|\bcaci\b|mantech|peraton|palantir|anduril|huntington ingalls|"
    r"textron|parsons corporation|\bkbr\b)", re.I)
# Finance and sales titles. Grant and budget-adjacent titles that could be research work are left to the reader.
FINANCE_SALES_TITLE = re.compile(
    r"\b(financ\w*|accounting|accountant|budget|revenue|payroll|treasury|investment|investor relations|"
    r"trading|trader|private equity|capital markets|portfolio manager|underwrit\w*|actuar\w*|"
    r"sales|account executive|business development|account manager)\b", re.I)
# Titles that are themselves policing or corrections jobs. Titles that only name the subject ("Research Analyst,
# Policing" at a reform nonprofit, "Police Accountability Analyst" at an oversight board) are left to the reader;
# excluded employers are caught by FORCE_ORG.
POLICING_TITLE = re.compile(r"\b(?:police|correction(?:s|al)?|peace|probation|parole|detention) (?:officer|sergeant|"
                            r"lieutenant|captain|cadet|aide|investigator)s?\b", re.I)
# A posting that names an advanced degree but also offers another way in (federal "qualify on education alone",
# "or an equivalent combination of education and experience") doesn't clearly require the degree.
EXPERIENCE_ROUTE = re.compile(
    r"(education alone|combination of (?:graduate )?education and (?:specialized )?experience|"
    r"equivalent combination|specialized experience|or (?:a )?bachelor'?s degree (?:and|plus|with)|"
    r"bachelor'?s degree (?:and|plus|with) \w+ years?|or equivalent (?:work )?experience|"
    r"qualify based on)", re.I)
EXCLUDED_PLACE = re.compile(r"\b(bronx|staten island|jersey city|hoboken)\b", re.I)

# Section headings inside descriptions (they arrive as one line, e.g. "Responsibilities: ...").
HEADING = re.compile(r"(?:(?<=[.!?:•])|^)\s*([A-Z][A-Za-z&/'’ ,()-]{2,60}):\s")
KEEP_HEADING = re.compile(
    r"(respons|dut(?:y|ies)|what you|problems you|your role|your impact|about the role|the role|job description|"
    r"summary|position|qualif|requir|skills|about you|good fit|who you are|education|degree|experience|ksas?\b|"
    r"field of study|work location|location|hybrid|work from home|remote|telework|salary|compensation|pay\b|"
    r"hours|schedule|residen|clearance|licens|certif)", re.I)
DROP_HEADING = re.compile(
    r"(^about (?!the role|you)|agency you|team you|benefit|why you should|why work|commitment to|loan forgiveness|"
    r"equal|eeo|diversity|accessib|accommodation|fraud|to apply|how to apply|click|search for job|visa|"
    r"guidance on candidates|including$|california|privacy|disclaimer|current office employees)", re.I)
KEY_FACT = re.compile(
    r"(remote|hybrid|telework|telecommut|in[- ]office|on[- ]?site|days? (?:a|per|each) week|salary|\$\s?\d|"
    r"degree|bachelor|master|ph\.?\s?d|doctora|\byears?\b|clearance|licens|certif|residen|deadline|"
    r"apply by|closing date|full[- ]time|part[- ]time|health (?:insurance|benefits|coverage))", re.I)
SENTENCE = re.compile(r"(?<=[.!?•])\s+")


def parse_time(value):
    if not value:
        return None
    if len(value) == 10:  # a plain date: start of that day, UTC
        value += "T00:00:00+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _places(location):
    return [p.strip(" ,-()") for p in _SPLIT.split(location or "") if p.strip(" ,-()")]


def drop_reason(job):
    """(reason, detail) when the posting fails a hard filter outright, else None. Clear cases only."""
    org, title = job.get("org") or "", job.get("title") or ""
    desc = job.get("description") or ""
    top = job.get("salary_max")
    if top and 1000 <= top < PAY_FLOOR:  # values under 1,000 are hourly rates; the reader judges those
        return "pay below the $60K floor", f"tops out at ${top:,.0f}"
    if (job.get("advanced_degree_required") or job.get("phd_required")) and not EXPERIENCE_ROUTE.search(desc):
        return "advanced degree required", job.get("advanced_degree_required") or "PhD"
    if (job.get("years_required") or 0) >= MAX_YEARS:
        return "5+ years required", f"{job['years_required']} years"
    if FORCE_ORG.search(org) or FORCE_TITLE.search(title) or POLICING_TITLE.search(title):
        return "policing, corrections, intelligence, or military employer or role", None
    if DEFENSE_CONTRACTOR.search(org):
        return "defense contractor", None
    if AI_EMPLOYER.search(org) and not re.search(r"deepmind", org, re.I):
        return "frontier AI or big-tech AI employer", None
    if CHARTER.search(org) or CHARTER.search(title):
        return "charter or private school", None
    if FINANCE_SALES_TITLE.search(title):
        return "finance or sales title", None
    if not job.get("remote"):
        places = _places(job.get("location"))
        if places and all(EXCLUDED_PLACE.search(p) for p in places):
            return "in-person or hybrid worksite in the Bronx, Staten Island, Jersey City, or Hoboken", None
        if places and all(DC_AREA.search(p) and not NYC_AREA.search(p) for p in places) \
                and _NOT_REMOTE.search(desc) and not _HYBRID.search(desc):
            return "DC-area role that is fully on-site", None
    return None


def flags(job):
    """Notes for the reader on things this screen couldn't settle."""
    out = []
    top = job.get("salary_max")
    if not top:
        out.append("pay not listed")
    elif top < 1000:
        out.append(f"hourly pay listed (${top:,.2f}/hr); check the annual equivalent against the floors")
    elif top < DS_PAY_FLOOR:
        out.append("pay tops out below $80K: bridge track only")
    if (job.get("advanced_degree_required") or job.get("phd_required")) and EXPERIENCE_ROUTE.search(job.get("description") or ""):
        out.append(f"{job.get('advanced_degree_required') or 'PhD'} named, but the posting also offers an experience route; check")
    if re.search(r"deepmind", job.get("org") or "", re.I):
        out.append("DeepMind: check it isn't a Gemini or product role")
    places = _places(job.get("location"))
    if not job.get("remote") and not places:
        out.append("worksite not stated")
    if not job.get("remote") and any(DC_AREA.search(p) for p in places) and not _HYBRID.search(job.get("description") or ""):
        out.append("DC area: check in-office days")
    if not job.get("remote") and places and not any(NYC_AREA.search(p) or DC_AREA.search(p) or ANYWHERE.search(p)
                                                     for p in places):
        out.append("location outside NYC and DC with no remote statement found; check it")
    return out


def trim(description):
    """Keep the role summary, the duties and requirements sections, and any sentence elsewhere that states pay,
    schedule, remote or hybrid days, degrees, years, licenses, clearance, residency, or deadlines."""
    text = re.sub(r"\s+", " ", description or "").strip()
    marks = [(m.start(1), m.group(1)) for m in HEADING.finditer(text)]
    if len(marks) < 2:
        head, sections = text[:SUMMARY_CHARS], [("", text[SUMMARY_CHARS:])]
    else:
        head = text[:marks[0][0]][:SUMMARY_CHARS]
        sections = [(name, text[start:(marks[i + 1][0] if i + 1 < len(marks) else len(text))])
                    for i, (start, name) in enumerate(marks)]
    kept = [head.strip()] if head.strip() else []
    for name, body in sections:
        if name and KEEP_HEADING.search(name) and not DROP_HEADING.search(name):
            kept.append(body.strip())
        else:  # a dropped or unlabeled section can still hold one fact the reader needs
            kept.extend(s.strip() for s in SENTENCE.split(body) if KEY_FACT.search(s) and len(s) < 400)
    out = " ".join(dict.fromkeys(k for k in kept if k))  # drop exact repeats, keep order
    return out if len(out) <= TRIM_CHARS else out[:TRIM_CHARS].rsplit(" ", 1)[0] + " …"


CANDIDATE_FIELDS = ("id", "org", "category", "tier", "source", "title", "location", "remote", "url", "posted",
                    "first_seen", "first_seen_at", "employment_type", "department", "salary_min", "salary_max",
                    "salary_text", "years_required", "nyc_job_id", "civil_service_title", "title_classification",
                    "career_level", "post_until", "exam_required")


def screen(feed, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    candidates, dropped = [], []
    for job in feed.get("jobs", []):
        seen_at = parse_time(job.get("first_seen_at") or job.get("first_seen"))
        if not seen_at or seen_at <= since:
            continue
        result = drop_reason(job)
        if result:
            reason, detail = result
            dropped.append({"org": job.get("org"), "title": job.get("title"), "reason": reason,
                            **({"detail": detail} if detail else {}), "url": job.get("url")})
            continue
        row = {k: job.get(k) for k in CANDIDATE_FIELDS if job.get(k) is not None}
        row["flags"] = flags(job)
        row["requirements_and_duties"] = trim(job.get("description"))
        row["description_chars"] = len(job.get("description") or "")
        candidates.append(row)
    candidates.sort(key=lambda r: r.get("first_seen_at") or r.get("first_seen") or "", reverse=True)
    counts = {}
    for d in dropped:
        counts[d["reason"]] = counts.get(d["reason"], 0) + 1
    return {
        "generated_at": feed.get("generated_at"),
        "screened_at": now.isoformat(timespec="seconds"),
        "window_start": since.isoformat(timespec="seconds"),
        "sources_ok": feed.get("sources_ok"),
        "sources_total": feed.get("sources_total"),
        "candidate_count": len(candidates),
        "dropped_count": len(dropped),
        "dropped_by_reason": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "candidates": candidates,
        "dropped": dropped,
    }


def main():
    feed = json.loads((DATA / "jobs.json").read_text())
    out = screen(feed)
    (DATA / "daily_candidates.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"{out['candidate_count']} candidates, {out['dropped_count']} dropped in the {WINDOW_DAYS}-day window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
