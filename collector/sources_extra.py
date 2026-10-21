"""Readers for hiring platforms whose job lists aren't simple public JSON feeds.

ADP Workforce Now, Oracle Recruiting Cloud, Paylocity, Pinpoint, JazzHR, iCIMS, Jobvite,
TalentBrew (Radancy) career sites, Greenhouse boards with the API turned off, and a generic
"links" reader for careers pages that link straight to individual postings (used for Paycom
and any platform the collector can't otherwise read).

Each reader returns raw job dicts in the same shape as the readers in sources.py.
"""
import html as htmllib
import json
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse
from xml.etree import ElementTree

from . import http
from .text import extract_salary, html_to_text

# ---------- detection (supported platforms; checked before the generic patterns' unsupported list) ----------

PATTERNS = [
    ("adp", re.compile(r"workforcenow\.adp\.com/[^\"'\s<>]*?[?&](?:amp;)?cid=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)),
    ("oracle", re.compile(r"([a-z0-9]+\.fa\.[a-z0-9]+\.oraclecloud\.com)(?::443)?/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/sites/([A-Za-z0-9_]+)", re.I)),
    ("paylocity", re.compile(r"recruiting\.paylocity\.com/recruiting/(?:jobs/All|PublicLeads/New)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)),
    ("pinpoint", re.compile(r"([a-z0-9-]+)\.pinpointhq\.com", re.I)),
    ("jazzhr", re.compile(r"([a-z0-9-]+)\.applytojob\.com", re.I)),
    ("icims", re.compile(r"((?:careers|uscareers|jobs)-[a-z0-9-]+\.icims\.com)", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([a-z0-9-]+)", re.I)),
]

# Links that point straight at one posting, on platforms we otherwise can't list.
JOB_LINK = re.compile(
    r"https?://(?:www\.)?(?:"
    r"paycomonline\.net/v4/ats/web\.php/(?:portal/[A-F0-9]{32}/)?jobs/\d+[^\"'\s<>]*|"
    r"recruiting\.paylocity\.com/recruiting/jobs/Details/\d+[^\"'\s<>]*|"
    r"[a-z0-9-]+\.applytojob\.com/apply/[A-Za-z0-9]{6,}/[^\"'\s<>]*|"
    r"[a-z0-9-]+\.icims\.com/jobs/\d+/[^\"'\s<>]*|"
    r"jobs\.jobvite\.com/[a-z0-9-]+/job/[A-Za-z0-9]+[^\"'\s<>]*|"
    r"[a-z0-9-]+\.pinpointhq\.com/(?:en/)?postings/[^\"'\s<>]+|"
    r"recruiting\d*\.ultipro\.com/[^\"'\s<>]*OpportunityDetail[^\"'\s<>]*"
    r")", re.I)

TALENTBREW_JOB = re.compile(r"""href=["'](/job/[^"']+/\d+/\d+)["']""", re.I)


def has_job_links(page):
    return bool(JOB_LINK.search(page or ""))


# ---------- shared helpers ----------

def _job(title, location, url, *, remote=None, posted=None, department=None, employment_type=None,
         description="", detail=None, salary_min=None, salary_max=None, salary_text=None):
    if salary_min is None:
        salary_min, salary_max, salary_text = extract_salary(description)
    return {"title": htmllib.unescape((title or "").strip()), "location": (location or "").strip(),
            "remote": remote, "url": url, "posted": (posted or "")[:10] or None, "department": department,
            "employment_type": employment_type, "salary_min": salary_min, "salary_max": salary_max,
            "salary_text": salary_text, "description": description, "_detail": detail}


_SCRIPTS = re.compile(r"<(script|style|noscript|nav|header|footer)\b.*?</\1>", re.I | re.S)


def page_text(raw):
    return html_to_text(_SCRIPTS.sub(" ", raw or ""))


def page_title(raw):
    for pat in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, raw or "", re.I | re.S)
        if m:
            text = html_to_text(m.group(1))
            text = re.split(r"\s+[|–—-]\s+", text)[0].strip()
            if text and text.lower() not in {"job details", "careers", "job opportunities"}:
                return text
    return ""


# ---------- ADP Workforce Now ----------

ADP_BASE = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"


def _adp_params(cid, **extra):
    return {"cid": cid, "timeStamp": int(time.time() * 1000), "lang": "en_US", "ccId": "19000101_000001",
            "locale": "en_US", **extra}


def fetch_adp(cid):
    jobs, skip = [], 0
    while skip < 1000:
        data = http.get_json(ADP_BASE, params=_adp_params(cid, **{"$top": 50, "$skip": skip}))
        reqs = data.get("jobRequisitions", [])
        for r in reqs:
            item = r.get("itemID")
            locs = []
            for loc in r.get("requisitionLocations", []) or []:
                addr = loc.get("address") or {}
                city = addr.get("cityName")
                state = (addr.get("countrySubdivisionLevel1") or {}).get("codeValue")
                name = (loc.get("nameCode") or {}).get("shortName")
                locs.append(", ".join(x for x in [city, state] if x) or name or "")
            loc_text = "; ".join(x for x in locs if x)
            url = (f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}"
                   f"&ccId=19000101_000001&jobId={item}&lang=en_US")
            jobs.append(_job(r.get("requisitionTitle"), loc_text, url,
                             remote=("remote" in loc_text.lower()) or None, posted=r.get("postDate"),
                             detail=("adp", (cid, item))))
        total = (data.get("meta") or {}).get("totalNumber", 0)
        skip += 50
        if not reqs or skip >= total:
            return jobs
    return jobs


def adp_detail(cid, item):
    data = http.get_json(f"{ADP_BASE}/{item}", params=_adp_params(cid))
    return html_to_text(data.get("requisitionDescription", ""))


# ---------- Oracle Recruiting Cloud (Candidate Experience) ----------

ORACLE_TERMS = ["data", "research", "analyst", "evaluation", "statistic", "psychometric", "assessment"]


def _oracle_site_number(host, site):
    if site.upper().startswith("CX"):
        return site
    raw = http.get_text(f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/requisitions")
    m = re.search(r'data-sitenumber="([^"]+)"', raw)
    return m.group(1) if m else site


def fetch_oracle(token):
    host, site = token.split("|")
    site_number = _oracle_site_number(host, site)
    found = {}
    for term in ORACLE_TERMS:
        finder = f'findReqs;siteNumber={site_number},limit=100,offset=0,keyword="{term}",sortBy=POSTING_DATES_DESC'
        data = http.get_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                             params={"onlyData": "true", "expand": "requisitionList.secondaryLocations",
                                     "finder": finder})
        for block in data.get("items", []):
            for r in block.get("requisitionList", []) or []:
                rid = r.get("Id")
                if not rid or rid in found:
                    continue
                url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}"
                loc = r.get("PrimaryLocation") or ""
                found[rid] = _job(r.get("Title"), loc, url,
                                  remote=(r.get("WorkplaceTypeCode") == "ORA_REMOTE" or "remote" in loc.lower()) or None,
                                  posted=r.get("PostedDate"), description=html_to_text(r.get("ShortDescriptionStr", "")),
                                  detail=("oracle", (host, site_number, rid)))
    return list(found.values())


def oracle_detail(host, site_number, rid):
    data = http.get_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                         params={"expand": "all", "onlyData": "true",
                                 "finder": f'ById;Id="{rid}",siteNumber={site_number}'})
    items = data.get("items") or [{}]
    item = items[0]
    return html_to_text(" ".join(item.get(k) or "" for k in
                                 ("ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr")))


# ---------- Paylocity ----------

def _balanced_json(raw, marker):
    start = raw.find(marker)
    if start < 0:
        return None
    i = raw.find("{", start)
    depth, in_str, esc = 0, False, False
    for j in range(i, len(raw)):
        ch = raw[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[i:j + 1])
    return None


def _find_job_lists(obj):
    """Lists of dicts inside a JSON blob that look like job postings."""
    found = []
    if isinstance(obj, dict):
        for v in obj.values():
            found.extend(_find_job_lists(v))
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any(
                any(k.lower() in ("jobtitle", "title") for k in x) for x in obj):
            found.append(obj)
        for v in obj:
            found.extend(_find_job_lists(v))
    return found


def fetch_paylocity(guid):
    raw = http.get_text(f"https://recruiting.paylocity.com/recruiting/jobs/All/{guid}")
    data = _balanced_json(raw, "window.pageData")
    jobs = []
    for lst in _find_job_lists(data or {}):
        for j in lst:
            title = j.get("JobTitle") or j.get("jobTitle") or j.get("Title") or j.get("title")
            jid = j.get("JobId") or j.get("jobId") or j.get("Id") or j.get("id")
            if not title or not jid:
                continue
            loc = j.get("LocationName") or j.get("locationName") or ""
            if isinstance(j.get("JobLocation"), dict):
                jl = j["JobLocation"]
                loc = loc or ", ".join(x for x in [jl.get("City"), jl.get("State")] if x)
            url = f"https://recruiting.paylocity.com/recruiting/jobs/Details/{jid}"
            jobs.append(_job(title, loc, url, remote=bool(j.get("IsRemote")) or None,
                             posted=j.get("PublishedDate") or j.get("publishedDate"), detail=("page", url)))
        if jobs:
            break
    if not jobs:  # fall back to links on the page
        jobs = fetch_links(f"https://recruiting.paylocity.com/recruiting/jobs/All/{guid}", raw=raw)
    return jobs


# ---------- Pinpoint (RSS) ----------

def fetch_pinpoint(token):
    raw = http.get_text(f"https://{token}.pinpointhq.com/jobs.rss")
    root = ElementTree.fromstring(raw.encode("utf-8"))
    jobs = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = html_to_text(item.findtext("description") or "")
        jobs.append(_job(title, "", link, posted=None, description=desc))
    return jobs


# ---------- JazzHR ----------

_JAZZ_ITEM = re.compile(r"""<h3[^>]*list-group-item-heading[^>]*>\s*<a href=["']([^"']+)["'][^>]*>(.*?)</a>\s*</h3>(.*?)(?=<h3|</ul>\s*</div>|$)""", re.I | re.S)


def fetch_jazzhr(token):
    raw = http.get_text(f"https://{token}.applytojob.com/apply")
    jobs = []
    for href, title, rest in _JAZZ_ITEM.findall(raw):
        bits = [html_to_text(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", rest, re.S)]
        loc = next((b for b in bits if re.search(r",\s*[A-Z]{2}\b|remote", b, re.I)), "")
        jobs.append(_job(html_to_text(title), loc, href, remote=("remote" in loc.lower()) or None,
                         detail=("page", href)))
    if not jobs:
        jobs = fetch_links(f"https://{token}.applytojob.com/apply", raw=raw)
    return jobs


# ---------- iCIMS ----------

_ICIMS_JOB = re.compile(r"""href=["'](https?://[^"']+?\.icims\.com/jobs/(\d+)/([^/"'?]+)/job[^"']*)["'][^>]*>(.*?)</a>""", re.I | re.S)


def fetch_icims(host, max_pages=15):
    found = {}
    for page in range(max_pages):
        raw = http.get_text(f"https://{host}/jobs/search", params={"ss": 1, "in_iframe": 1, "pr": page})
        before = len(found)
        for href, jid, slug, label in _ICIMS_JOB.findall(raw):
            title = html_to_text(label) or slug.replace("-", " ").title()
            if jid in found or len(title) < 3:
                continue
            ctx = raw[raw.find(href):raw.find(href) + 1500]
            loc_m = re.search(r"(?:Location|Job Locations?)\s*</[^>]+>\s*<[^>]+>\s*([^<]{2,80})", ctx, re.I)
            loc = html_to_text(loc_m.group(1)) if loc_m else ""
            url = href.split("?")[0]
            found[jid] = _job(title, loc, url, remote=("remote" in loc.lower()) or None,
                              detail=("page", url + "?in_iframe=1"))
        if len(found) == before:
            break
    return list(found.values())


# ---------- Jobvite ----------

_JOBVITE_ROW = re.compile(r"""<a href=["'](/[a-z0-9-]+/job/[A-Za-z0-9]+)["'][^>]*>(.*?)</a>(.*?)</tr>""", re.I | re.S)


def fetch_jobvite(token):
    raw = http.get_text(f"https://jobs.jobvite.com/{token}/jobs")
    jobs, seen = [], set()
    for path, title, rest in _JOBVITE_ROW.findall(raw):
        if path in seen:
            continue
        seen.add(path)
        loc_m = re.search(r"jv-job-list-location[^>]*>(.*?)</td>", rest, re.S)
        loc = html_to_text(loc_m.group(1)) if loc_m else ""
        url = "https://jobs.jobvite.com" + path
        jobs.append(_job(html_to_text(title), loc, url, remote=("remote" in loc.lower()) or None,
                         detail=("page", url)))
    return jobs


# ---------- TalentBrew (Radancy) career sites, e.g. careers.mathematica.org ----------

def fetch_talentbrew(base, max_pages=10):
    found = {}
    for page in range(1, max_pages + 1):
        data = http.get_json(f"{base.rstrip('/')}/search-jobs/results", params={
            "ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": 100, "Distance": 50,
            "SearchResultsModuleName": "Search Results", "SearchFiltersModuleName": "Search Filters",
            "SortCriteria": 0, "SortDirection": 0, "SearchType": 5, "ResultsType": 0})
        raw = data.get("results", "") if isinstance(data, dict) else ""
        before = len(found)
        for m in re.finditer(r"""<a[^>]+href=["'](/job/[^"']+)["'][^>]*>(.*?)</a>""", raw, re.S):
            path, inner = m.group(1), m.group(2)
            if path in found:
                continue
            title_m = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.S)
            loc_m = re.search(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</', inner, re.S)
            title = html_to_text(title_m.group(1) if title_m else inner)
            loc = html_to_text(loc_m.group(1)) if loc_m else ""
            url = base.rstrip("/") + path
            found[path] = _job(title, loc, url, remote=("remote" in (loc + title).lower()) or None,
                               detail=("page", url))
        if len(found) == before:
            break
    return list(found.values())


# ---------- Greenhouse boards with the public API turned off ----------

def fetch_greenhouse_html(token):
    raw = http.get_text(f"https://job-boards.greenhouse.io/{token}")
    jobs, seen = [], set()
    for m in re.finditer(r"""href=["'](https://job-boards\.greenhouse\.io/%s/jobs/\d+)[^"']*["'][^>]*>(.*?)</a>""" % re.escape(token),
                         raw, re.S | re.I):
        url, inner = m.group(1), m.group(2)
        if url in seen:
            continue
        seen.add(url)
        parts = [html_to_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", inner, re.S)]
        title = parts[0] if parts else html_to_text(inner)
        loc = parts[1] if len(parts) > 1 else ""
        jobs.append(_job(title, loc, url, remote=("remote" in loc.lower()) or None, detail=("page", url)))
    return jobs


# ---------- generic: careers page that links straight to postings ----------

def fetch_links(url, raw=None, limit=60):
    raw = raw if raw is not None else http.get_text(url)
    jobs, seen = [], set()
    for m in JOB_LINK.finditer(raw):
        link = htmllib.unescape(m.group(0)).rstrip("\\);,.")
        key = link.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        # Use the anchor text when the link is inside an <a> with a readable label.
        a = re.search(r"""<a[^>]+href=["']%s[^"']*["'][^>]*>(.*?)</a>""" % re.escape(m.group(0)[:60]), raw, re.S)
        label = html_to_text(a.group(1)) if a else ""
        jobs.append(_job(label if 3 <= len(label) <= 120 else "", "", link, detail=("page", link)))
        if len(jobs) >= limit:
            break
    return jobs


def page_detail(url):
    raw = http.get_text(url)
    return page_title(raw), page_text(raw)[:8000]


ADAPTERS = {
    "adp": fetch_adp,
    "oracle": fetch_oracle,
    "paylocity": fetch_paylocity,
    "pinpoint": fetch_pinpoint,
    "jazzhr": fetch_jazzhr,
    "icims": fetch_icims,
    "jobvite": fetch_jobvite,
    "talentbrew": fetch_talentbrew,
    "greenhouse_html": fetch_greenhouse_html,
    "links": fetch_links,
}
