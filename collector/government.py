"""Government job sources: NYC Jobs (NYC Open Data) and, optionally, USAJOBS."""
import os

from . import http
from .text import NYC_GOV_TITLES, TITLE_EXCLUDE, iso_date

NYC_DATASET = "https://data.cityofnewyork.us/resource/kpav-sd4t.json"
NYC_FIELDS = ("job_id,agency,business_title,civil_service_title,title_classification,career_level,"
              "salary_range_from,salary_range_to,salary_frequency,full_time_part_time_indicator,"
              "work_location,division_work_unit,job_description,minimum_qual_requirements,"
              "preferred_skills,posting_date,post_until,posting_type")


def _nyc_link(job_id):
    return f"https://cityjobs.nyc.gov/jobs?q={job_id}"


def fetch_nyc(limit=10000):
    """All external NYC Jobs postings, narrowed to data/research/analyst titles."""
    rows = http.get_json(NYC_DATASET, params={
        "$select": NYC_FIELDS,
        "$where": "posting_type = 'External'",
        "$order": "posting_date DESC",
        "$limit": limit,
    }, timeout=60)
    total = len(rows)
    jobs, seen_ids = [], set()
    for r in rows:
        title = r.get("business_title", "")
        cs_title = r.get("civil_service_title", "")
        if r.get("job_id") in seen_ids:
            continue
        if not (NYC_GOV_TITLES.search(title) or NYC_GOV_TITLES.search(cs_title)):
            continue
        if TITLE_EXCLUDE.search(title):
            continue
        seen_ids.add(r.get("job_id"))
        annual = (r.get("salary_frequency") or "").lower() == "annual"
        desc = " ".join(x for x in [r.get("job_description"), "Minimum qualifications: " + (r.get("minimum_qual_requirements") or ""),
                                    "Preferred skills: " + (r.get("preferred_skills") or "")] if x)
        jobs.append({
            "title": title.strip(),
            "location": r.get("work_location") or "New York, NY",
            "remote": None,
            "url": _nyc_link(r.get("job_id")),
            "posted": (r.get("posting_date") or "")[:10] or None,
            "department": r.get("division_work_unit"),
            "employment_type": {"F": "Full-time", "P": "Part-time"}.get(r.get("full_time_part_time_indicator"),
                                                                          r.get("full_time_part_time_indicator")),
            "salary_min": float(r["salary_range_from"]) if annual and r.get("salary_range_from") else None,
            "salary_max": float(r["salary_range_to"]) if annual and r.get("salary_range_to") else None,
            "salary_text": f"${r.get('salary_range_from')}–${r.get('salary_range_to')} ({r.get('salary_frequency')})",
            "description": desc,
            "_detail": None,
            "org": r.get("agency", "NYC agency").title(),
            "extra": {
                "nyc_job_id": r.get("job_id"),
                "civil_service_title": cs_title,
                "title_classification": r.get("title_classification"),
                "career_level": r.get("career_level"),
                "post_until": iso_date((r.get("post_until") or "").split("T")[0].strip()),
                "exam_required": "Competitive" in (r.get("title_classification") or ""),
            },
        })
    return total, jobs


USAJOBS_KEYWORDS = ["data scientist", "statistician", "research analyst", "education program", "social science analyst"]


def fetch_usajobs():
    """Federal postings in the NYC area or remote. Needs a free API key from
    developer.usajobs.gov stored as USAJOBS_API_KEY and USAJOBS_EMAIL; skipped otherwise."""
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        return None, []
    headers = {"Authorization-Key": key, "User-Agent": email, "Host": "data.usajobs.gov"}
    found = {}
    for kw in USAJOBS_KEYWORDS:
        for loc_params in ({"LocationName": "New York, New York", "Radius": 25}, {"RemoteIndicator": "True"}):
            data = http.get_json("https://data.usajobs.gov/api/search", headers=headers,
                                 params={"Keyword": kw, "ResultsPerPage": 250, **loc_params})
            for item in data.get("SearchResult", {}).get("SearchResultItems", []):
                d = item.get("MatchedObjectDescriptor", {})
                url = d.get("PositionURI")
                if not url or url in found:
                    continue
                pay = (d.get("PositionRemuneration") or [{}])[0]
                annual = pay.get("RateIntervalCode") == "PA"
                summary = (d.get("UserArea", {}).get("Details", {}) or {}).get("JobSummary", "")
                quals = d.get("QualificationSummary", "")
                found[url] = {
                    "title": d.get("PositionTitle", ""),
                    "location": d.get("PositionLocationDisplay", ""),
                    "remote": loc_params.get("RemoteIndicator") == "True" or None,
                    "url": url,
                    "posted": (d.get("PublicationStartDate") or "")[:10] or None,
                    "department": d.get("DepartmentName"),
                    "employment_type": ", ".join(s.get("Name", "") for s in d.get("PositionSchedule", [])),
                    "salary_min": float(pay["MinimumRange"]) if annual and pay.get("MinimumRange") else None,
                    "salary_max": float(pay["MaximumRange"]) if annual and pay.get("MaximumRange") else None,
                    "salary_text": f"${pay.get('MinimumRange')}–${pay.get('MaximumRange')} {pay.get('Description', '')}",
                    "description": f"{summary} Qualifications: {quals}",
                    "_detail": None,
                    "org": d.get("OrganizationName", "Federal agency"),
                    "extra": {"closes": (d.get("ApplicationCloseDate") or "")[:10] or None},
                }
    return len(found), list(found.values())

