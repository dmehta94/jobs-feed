"""Broad keyword searches across job aggregators, to catch employers not in orgs.csv.

Aggregators return every kind of data job, so results must pass two filters: the
usual title filter, and a mission filter requiring education or public-interest
language in the employer name, title, or description.

Sources:
    The Muse     public API, no key
    Remotive     public API, no key (remote roles only)
    Adzuna       free API key (ADZUNA_APP_ID, ADZUNA_APP_KEY); skipped without one
"""
import os
import re
from datetime import date, timedelta

from . import http
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
]


def has_mission(job):
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
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return None
    found = {}
    for term in SEARCH_TERMS:
        data = http.get_json("https://api.adzuna.com/v1/api/jobs/us/search/1", params={
            "app_id": app_id, "app_key": app_key, "what": term, "where": "New York, NY", "distance": 25,
            "results_per_page": 50, "max_days_old": max_days_old, "content-type": "application/json",
        })
        for r in data.get("results", []):
            url = r.get("redirect_url")
            if not url or url in found:
                continue
            found[url] = _job((r.get("company") or {}).get("display_name"), r.get("title"),
                              (r.get("location") or {}).get("display_name"), url, posted=r.get("created"),
                              description=html_to_text(r.get("description", "")),
                              employment_type=r.get("contract_time"),
                              salary_min=r.get("salary_min") if r.get("salary_is_predicted") in ("0", 0) else None,
                              salary_max=r.get("salary_max") if r.get("salary_is_predicted") in ("0", 0) else None)
    return list(found.values())


SOURCES = [
    ("The Muse", fetch_themuse),
    ("Remotive", fetch_remotive),
    ("Adzuna", fetch_adzuna),
]


def recent_enough(job, today=None, days=30):
    """Aggregators keep stale listings around; drop anything dated more than a month back."""
    if not job.get("posted"):
        return True
    today = today or date.today()
    return job["posted"] >= (today - timedelta(days=days)).isoformat()
