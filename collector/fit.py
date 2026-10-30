"""Rough fit scoring for an early-career education data/research candidate.

The collector keeps anything plausibly relevant; this module decides what is worth a person's
attention. It drops senior and off-target titles outright, then scores the rest on role match,
education focus, and seniority so the weekly email can show a short ranked list.
"""
import re

# Titles that read as senior but are early-career in research organizations.
NOT_SENIOR = re.compile(r"\b(?:senior research (?:assistant|associate i?\b)|(?:research )?data manager\b)", re.I)
SENIOR = re.compile(
    r"\b(senior|sr\.?|lead|principal|director|head|chief|vp|vice president|manager|architect|"
    r"supervis\w*|commissioner|deputy|executive|dean|provost|staff (?:engineer|scientist|data|research))\b", re.I)

# Functions that share words like "analyst" or "data" but aren't the target work.
OFF_TARGET = re.compile(
    r"\b(financ\w*|budget|revenue|accounting|accountant|procurement|payroll|human resources|hr|"
    r"recruit\w*|talent|legal|litigation|attorney|paralegal|compliance|audit\w*|tax|"
    r"contracts? (?:manager|specialist|administrator|analyst|officer)|contracting|"
    r"it|information technology|systems? analyst|servicenow|salesforce|erp|network|security|cyber|cloud|"
    r"infrastructure|data center|devops|site reliability|software|engineer\w*|developer|"
    r"program manager|project manager|product manager|operations|facilities|clerical|administrative assistant|"
    r"sales|account|marketing|communications|fundrais\w*|development officer|grants?|"
    r"intern(ship)?|extern(ship)?|scoring service|reader|evaluator - scoring|nurse|clinic\w*|"
    r"programmer|mainframe|business analyst|systems integration|database administrator|"
    r"uipath|rpa|robotic process|automation|timekeep\w*|time ?keeper|"
    r"enforcement|safeguards|trust and safety|investigat\w*|police|correction|"
    r"adjunct|faculty|lecturer|professor|postdoc\w*|tenure)\b", re.I)

# Dev doesn't work for charter or private schools; these networks don't always say "charter" in their name.
CHARTER = re.compile(r"\b(charter|success academy|uncommon schools|kipp|achievement first|democracy prep|"
                     r"idea public schools|harlem village academies|great oaks|ascend learning|brooklyn laboratory|"
                     r"zeta charter|public prep|explore schools|icahn charter|private school|preparatory school)\b", re.I)

CORE_ROLE = re.compile(
    r"\b(data analyst|research analyst|research associate|research assistant|research scientist|"
    r"quantitative|data scientist|statistician|statistic\w*|evaluation|evaluator|efficacy|psychometric\w*|"
    r"assessment|measurement|learning scien\w*|learning engineer\w*|institutional research|"
    r"analytics|analyst|researcher|insights|policy analyst|data (?:and|&) (?:systems|research|evaluation)|"
    r"data (?:specialist|coordinator|associate|fellow|manager|strategist)|"
    r"research (?:specialist|coordinator|fellow|advisor|data|program associate)|"
    r"(?:demographic|social|education(?:al)?|learning|assessment|survey) scientist|"
    r"fellows? program,? .*(?:economics|policy|societal|education))\b", re.I)

CURRICULUM = re.compile(r"\b(curriculum|instructional design\w*|content (?:creator|developer|specialist))\b", re.I)

EDUCATION = re.compile(
    r"\b(education\w*|schools?|students?|teach(?:er|ers|ing)|curricul\w*|k-?12|classroom\w*|literacy|"
    r"learners?|school district\w*|edtech|assessment)\b", re.I)
# Degree boilerplate ("accredited college", "high school diploma", "education and experience") isn't an education focus.
BOILERPLATE = re.compile(r"(high school|accredited (?:college|university)|education(?:al)? (?:and|or|/) ?experience|"
                         r"education requirements?|equivalent education|level of education|degree in education)", re.I)

ENTRY = re.compile(r"\b(entry[- ]level|junior|new grad\w*|early[- ]career)\b", re.I)
EARLY = re.compile(r"\b(associate|assistant|coordinator|level (?:i|1)|analyst i)\b", re.I)

AI_LAB_OK = re.compile(r"\b(education|learning|societal|economic|social|policy|public interest|impact)\b", re.I)
AI_LABS = re.compile(r"\b(anthropic|deepmind)\b", re.I)

CATEGORY_POINTS = {"Education": 3, "Research & Policy": 2, "Higher Ed": 2, "Civic Tech": 2,
                   "NYC Government": 1, "Federal Government": 0, "Discovered": 0, "AI & Tech": 0}


def title_gate(title):
    """Why a title is ruled out, or None if it's a data, research, or curriculum role at a fitting level."""
    title = title or ""
    if (SENIOR.search(title) and not NOT_SENIOR.search(title)) or OFF_TARGET.search(title):
        return "senior or off-target title"
    if not (CORE_ROLE.search(title) or CURRICULUM.search(title)):
        return "title isn't a data, research, or curriculum role"
    return None


def title_priority(title):
    """0 for titles worth showing, 1 for the rest; used to spend detail-page reads where they matter."""
    return 0 if title_gate(title) is None else 1


def score(job):
    """Return (score, reasons). None means 'don't show this posting at all'."""
    title = job.get("title") or ""
    text = BOILERPLATE.sub(" ", " ".join([title, (job.get("description") or "")[:4000]]))
    gate = title_gate(title)
    if gate:
        return None, [gate]
    core, curriculum = CORE_ROLE.search(title), CURRICULUM.search(title)
    if CHARTER.search(job.get("org") or ""):
        return None, ["charter or private school"]
    if AI_LABS.search(job.get("org") or "") and not AI_LAB_OK.search(title):
        return None, ["AI lab role outside education or societal impact"]

    points, why = CATEGORY_POINTS.get(job.get("category"), 0), []
    if core:
        points += 3
        why.append("data/research role")
    edu_hits = len(EDUCATION.findall(text))
    if not core and not (EDUCATION.search(title) or edu_hits >= 5):
        return None, ["curriculum or design role outside education (e.g. corporate training)"]
    if not core:
        points += 1
        why.append("curriculum role")
    if EDUCATION.search(title) or edu_hits >= 5:
        points += 3
        why.append("education focus")
    elif edu_hits >= 2:
        points += 1
    years = job.get("years_required")
    if years is not None:
        if years <= 2:
            points += 2
            why.append(f"asks {years} yrs")
        elif years >= 4:
            points -= 2
    if ENTRY.search(title):
        points += 3
        why.append("entry-level")
    elif EARLY.search(title):
        points += 1
    if job.get("tier") == 1:
        points += 1
    if job.get("salary_max") and job["salary_max"] >= 90000:
        points += 1
    return points, why


def shortlist(jobs, max_items=15, max_per_org=3, max_nyc=5, min_score=5):
    """Best-scoring postings, with caps so one employer or the city can't crowd the list."""
    scored = []
    for job in jobs:
        s, why = score(job)
        if s is not None and s >= min_score:
            scored.append((s, why, job))
    scored.sort(key=lambda t: (-t[0], t[2].get("tier", 9), t[2].get("org", "")))
    out, per_org, nyc = [], {}, 0
    for s, why, job in scored:
        org = job.get("org") or ""
        is_nyc = job.get("category") == "NYC Government"
        if per_org.get(org, 0) >= max_per_org or (is_nyc and nyc >= max_nyc):
            continue
        per_org[org] = per_org.get(org, 0) + 1
        nyc += is_nyc
        out.append({**job, "fit_score": s, "fit_reasons": why})
        if len(out) >= max_items:
            break
    return out, len(scored)
