"""Broad keyword searches across job aggregators, to catch employers not in orgs.csv.

Aggregators return every kind of data job, so results must pass two filters: the
usual title filter, and a mission filter requiring education or public-interest
language in the employer name, title, or description.

Sources:
    The Muse     public API, no key
    Remotive     public API, no key (remote roles only)
    Adzuna       free API key (ADZUNA_APP_ID, ADZUNA_APP_KEY); skipped without one
    Edtech.com   education job board; reads its sitemap of recent postings, then each relevant posting's
                 structured data (employer, location, remote, pay)
    HigherEdJobs remote-only keyword searches of the largest college and university job board
"""
import html as htmllib
import json
import os
import re
from datetime import date, timedelta

from . import http
from .fit import title_priority
from .text import extract_salary, html_to_text, location_is_ok, title_is_relevant

# Mission language, checked in the employer name, title, and the start of the description
# (later paragraphs are mostly boilerplate like "college degree" or "equity compensation").
MISSION = re.compile(
    r"\b(education\w*|schools?|students?|(?<!machine )(?<!deep )(?<!reinforcement )learning(?! models?)|"
    r"learners?|teach(er|ers|ing)|curricul\w*|edtech|k-?12|classrooms?|literacy|tutoring|"
    r"nonprofit|non-profit|public sector|civic|public interest|social impact|public policy|"
    r"philanthrop\w*)\b",
    re.I,
)
# Words that signal mission only when they're in the employer's name.
MISSION_ORG = re.compile(r"\b(universit\w*|college|foundation|institute|district|department|city of|state of|"
                         r"academy|council|alliance|trust|fund for)\b", re.I)

SEARCH_TERMS = [
    "education data analyst",
    "education data scientist",
    "education research analyst",
    "learning analytics",
    "learning scientist",
    "efficacy research",
    "psychometrician",
    "institutional research analyst",
    "assessment data analyst",
    "research associate education",
    "policy research analyst",
    "nonprofit data analyst",
    "data analyst school district",
    "institutional effectiveness analyst",
    "enrollment analyst college",
    "student success analyst",
    "assessment specialist",
    "measurement scientist",
    "program evaluation associate",
    "evaluation associate",
    "research assistant education",
    "learning engineer",
    "product analyst education",
]


def has_mission(job):
    if job.get("_mission"):  # education-only job boards
        return True
    text = " ".join([job.get("org") or "", job.get("title") or "", (job.get("description") or "")[:800]])
    return bool(MISSION.search(text) or MISSION_ORG.search(job.get("org") or ""))


def keep(job):
    return (title_is_relevant(job["title"]) and location_is_ok(job["location"], job["remote"])
            and has_mission(job))


def _job(org, title, location, url, *, remote=None, posted=None, description="", employment_type=None,
         salary_min=None, salary_max=None, salary_text=None):
    if salary_min is None:
        salary_min, salary_max, salary_text = extract_salary(description)
    return {"org": (org or "").strip(), "title": (title or "").strip(), "location": (location or "").strip(),
            "remote": remote, "url": url, "posted": (posted or "")[:10] or None, "department": None,
            "employment_type": employment_type, "salary_min": salary_min, "salary_max": salary_max,
            "salary_text": salary_text, "description": description, "_detail": None}


def fetch_themuse(max_pages=10):
    found = {}
    for page in range(max_pages):
        data = http.get_json("https://www.themuse.com/api/public/jobs", params=[
            ("page", page), ("category", "Data and Analytics"), ("category", "Data Science"),
            ("category", "Science and Engineering"), ("category", "Education"),
            ("location", "New York, NY"), ("location", "Flexible / Remote"),
            ("level", "Entry Level"), ("level", "Mid Level"),
        ])
        for r in data.get("results", []):
            url = (r.get("refs") or {}).get("landing_page")
            if not url or url in found:
                continue
            locs = ", ".join(loc.get("name", "") for loc in r.get("locations", []))
            found[url] = _job((r.get("company") or {}).get("name"), r.get("name"), locs, url,
                              remote=("remote" in locs.lower()) or None, posted=r.get("publication_date"),
                              description=html_to_text(r.get("contents", "")))
        if page + 1 >= data.get("page_count", 0):
            break
    return list(found.values())


US_OK = re.compile(r"\b(usa?|united states|americas|north america|worldwide|anywhere|global)\b", re.I)


def fetch_remotive():
    found = []
    for category in ("data", "education"):
        data = http.get_json("https://remotive.com/api/remote-jobs", params={"category": category})
        for r in data.get("jobs", []):
            where = r.get("candidate_required_location") or ""
            if where and not US_OK.search(where):
                continue
            desc = html_to_text(r.get("description", ""))
            smin, smax, stext = extract_salary(r.get("salary") or "")
            found.append(_job(r.get("company_name"), r.get("title"), where or "Remote", r.get("url"),
                              remote=True, posted=r.get("publication_date"), description=desc,
                              employment_type=r.get("job_type"), salary_min=smin, salary_max=smax,
                              salary_text=stext or r.get("salary") or None))
    return found


def fetch_adzuna(max_days_old=14):
    """Two passes per search term: jobs within 25 miles of NYC, and remote jobs anywhere in the US."""
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return None
    found = {}
    passes = [({"where": "New York, NY", "distance": 25}, None), ({"what_or": "remote telework"}, True)]
    for term in SEARCH_TERMS:
        for extra, remote in passes:
            data = http.get_json("https://api.adzuna.com/v1/api/jobs/us/search/1", params={
                "app_id": app_id, "app_key": app_key, "what": term, "results_per_page": 50,
                "max_days_old": max_days_old, "content-type": "application/json", **extra,
            })
            for r in data.get("results", []):
                url = r.get("redirect_url")
                if not url or url in found:
                    continue
                desc = html_to_text(r.get("description", ""))
                real_pay = r.get("salary_is_predicted") in ("0", 0)
                found[url] = _job((r.get("company") or {}).get("display_name"), r.get("title"),
                                  (r.get("location") or {}).get("display_name"), url, posted=r.get("created"),
                                  description=desc, employment_type=r.get("contract_time"),
                                  # Adzuna shows only a snippet, so "remote" must appear in the title or snippet.
                                  remote=(remote and bool(re.search(r"\b(remote|telework)\b", r.get("title", "") + desc, re.I)))
                                  or None,
                                  salary_min=r.get("salary_min") if real_pay else None,
                                  salary_max=r.get("salary_max") if real_pay else None)
    return list(found.values())


_SITEMAP_ENTRY = re.compile(r"<loc>(https://www\.edtech\.com/jobs/([a-z0-9-]+?)-\d+)</loc>\s*<lastmod>([^<]+)</lastmod>")
_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def job_posting_ld(page):
    """The schema.org JobPosting embedded in a posting page, or None."""
    for block in _LD_JSON.findall(page or ""):
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _ld_location(ld):
    places = ld.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    parts = []
    for place in places:
        addr = (place or {}).get("address") or {}
        if isinstance(addr, dict):
            bits = [addr.get("addressLocality"), addr.get("addressRegion")]
            parts.append(", ".join(b for b in bits if b))
    return "; ".join(p for p in parts if p)


def _ld_salary(ld):
    value = ((ld.get("baseSalary") or {}).get("value") or {}) if isinstance(ld.get("baseSalary"), dict) else {}
    unit = (value.get("unitText") or "").upper()
    lo, hi = value.get("minValue"), value.get("maxValue") or value.get("value")
    if unit not in ("", "YEAR") or not isinstance(hi, (int, float)):
        return None, None, None
    lo = lo if isinstance(lo, (int, float)) else hi
    return int(lo), int(hi), f"${int(lo):,}–${int(hi):,}"


def from_job_posting_ld(ld, url):
    desc = html_to_text(htmllib.unescape(ld.get("description") or ""))
    remote = (ld.get("jobLocationType") or "").upper() == "TELECOMMUTE" or None
    location = _ld_location(ld)
    if remote and "remote" not in location.lower():
        location = f"Remote ({location})" if location else "Remote"
    smin, smax, stext = _ld_salary(ld)
    org = ld.get("hiringOrganization") or {}
    etype = ld.get("employmentType")
    return _job(org.get("name") if isinstance(org, dict) else org, htmllib.unescape(ld.get("title") or ""), location,
                url, remote=remote, posted=ld.get("datePosted"), description=desc,
                employment_type=", ".join(etype) if isinstance(etype, list) else etype,
                salary_min=smin, salary_max=smax, salary_text=stext)


def fetch_edtech(max_details=120):
    """Recent postings from Edtech.com. The sitemap lists about a week of postings by URL slug, which
    carries the title, so only postings whose titles fit are opened."""
    xml = http.get_text("https://www.edtech.com/sitemap.xml")
    picks = []
    for url, slug, _lastmod in _SITEMAP_ENTRY.findall(xml):
        guess = slug.replace("-", " ")
        if title_is_relevant(guess) and title_priority(guess) == 0:
            picks.append(url)
    found = []
    for url in picks[:max_details]:
        try:
            ld = job_posting_ld(http.get_text(url))
        except http.FetchError:
            continue
        if ld:
            job = from_job_posting_ld(ld, url)
            job["_mission"] = True
            found.append(job)
    return found


HEJ_TERMS = ["data", "research", "analyst", "assessment", "institutional research", "evaluation", "curriculum",
             "statistician", "learning analytics"]
_HEJ_CARD = re.compile(r'<a href="details\.cfm\?JobCode=(\d+)[^"]*">([^<]+)</a>\s*<br>\s*([^<]+?)\s*<br>\s*([^<]+?)\s*<'
                       r'(.*?)Posted ([^<]+?)<br>', re.S)


def _hej_posted(text, today=None):
    today = today or date.today()
    t = text.strip().lower()
    if t == "today":
        return today.isoformat()
    if t == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = re.match(r"(\d+) days? ago", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.match(r"(\d{2})/(\d{2})/(\d{2,4})", t)
    if m:
        year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        return f"{year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def fetch_higheredjobs(max_details=60):
    """Remote postings only (RemoteType 2, 'Online/Remote'); on-site university jobs are rarely near NYC."""
    found = {}
    for term in HEJ_TERMS:
        page = http.get_text("https://www.higheredjobs.com/search/advanced_action.cfm",
                             params={"Keyword": term, "Remote": 2, "SortBy": 1, "NumJobs": 100})
        for code, title, org, loc, rest, posted in _HEJ_CARD.findall(page):
            if code in found:
                continue
            salary = re.search(r'class="job-salary">([^<]+)<', rest)
            smin, smax, stext = extract_salary(htmllib.unescape(salary.group(1))) if salary else (None, None, None)
            found[code] = _job(htmllib.unescape(org), htmllib.unescape(title), htmllib.unescape(loc),
                               f"https://www.higheredjobs.com/search/details.cfm?JobCode={code}", remote=True,
                               posted=_hej_posted(posted), salary_min=smin, salary_max=smax, salary_text=stext)
    jobs = list(found.values())
    details = 0
    for job in jobs:
        if details >= max_details:
            break
        if title_is_relevant(job["title"]) and title_priority(job["title"]) == 0:
            details += 1
            try:
                job["description"] = html_to_text(_hej_body(http.get_text(job["url"])))
            except http.FetchError:
                continue
            if job["salary_min"] is None:
                job["salary_min"], job["salary_max"], job["salary_text"] = extract_salary(job["description"])
    return jobs


def _hej_body(page):
    start = page.find('id="jobDesc"')
    if start < 0:
        return ""
    body = page[start + len('id="jobDesc"') + 1: start + 30000]
    return re.split(r"<footer|id=\"[^\"]*apply|class=\"[^\"]*apply", body, maxsplit=1, flags=re.I)[0]


SOURCES = [
    ("The Muse", fetch_themuse),
    ("Remotive", fetch_remotive),
    ("Adzuna", fetch_adzuna),
    ("Edtech.com", fetch_edtech),
    ("HigherEdJobs", fetch_higheredjobs),
]


def recent_enough(job, today=None, days=30):
    """Aggregators keep stale listings around; drop anything dated more than a month back."""
    if not job.get("posted"):
        return True
    today = today or date.today()
    return job["posted"] >= (today - timedelta(days=days)).isoformat()
