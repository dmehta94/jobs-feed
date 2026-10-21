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
    ("adp", re.compile(r"""recruitment-current-openings[^>]*cid=["']([0-9a-f-]{36})["'][^>]*ccid=["']([^"']+)["']""", re.I)),
    ("jazzhr", re.compile(r"app\.jazz\.co/widgets/basic/create/([a-z0-9-]+)", re.I)),
    ("rippling", re.compile(r"ats\.rippling\.com(?:/|&#x2F;)([a-z0-9-]+)(?:/|&#x2F;)jobs", re.I)),
    ("ukgready", re.compile(r"(secure\d*\.saashr\.com)/ta/(\d+)\.careers", re.I)),
    ("ukgpro", re.compile(r"(recruiting\d*\.ultipro\.com)(?::443)?/([A-Z0-9]+)/JobBoard/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)),
    ("csod", re.compile(r"([a-z0-9-]+)\.csod\.com/ux/ats/careersite/(\d+)", re.I)),
    ("adp", re.compile(r"workforcenow\.adp\.com/[^\"'\s<>]*?[?&](?:amp;)?cid=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)),
    ("oracle", re.compile(r"([a-z0-9]+\.fa\.[a-z0-9]+\.oraclecloud\.com)(?::443)?/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/sites/([A-Za-z0-9_]+)", re.I)),
    ("paylocity", re.compile(r"recruiting\.paylocity\.com/recruiting/(?:jobs/All|PublicLeads/New)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)),
    ("pinpoint", re.compile(r"([a-z0-9-]+)\.pinpointhq\.com", re.I)),
    ("jazzhr", re.compile(r"([a-z0-9-]+)\.applytojob\.com", re.I)),
    ("icims", re.compile(r"((?:careers|uscareers|jobs)-[a-z0-9-]+\.icims\.com)", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([a-z0-9-]+)", re.I)),
]

MULTI_GROUP = {"adp", "ukgready", "csod", "ukgpro"}  # platforms whose token joins several captured groups with "|"

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


def _adp_params(cid, ccid="19000101_000001", **extra):
    return {"cid": cid, "timeStamp": int(time.time() * 1000), "lang": "en_US", "ccId": ccid,
            "locale": "en_US", **extra}


def fetch_adp(token):
    cid, _, ccid = token.partition("|")
    ccid = ccid or "19000101_000001"
    jobs, skip = [], 0
    while skip < 1000:
        data = http.get_json(ADP_BASE, params=_adp_params(cid, ccid, **{"$top": 50, "$skip": skip}))
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
                   f"&ccId={ccid}&jobId={item}&lang=en_US")
            jobs.append(_job(r.get("requisitionTitle"), loc_text, url,
                             remote=("remote" in loc_text.lower()) or None, posted=r.get("postDate"),
                             detail=("adp", (cid, item, ccid))))
        total = (data.get("meta") or {}).get("totalNumber", 0)
        skip += 50
        if not reqs or skip >= total:
            return jobs
    return jobs


def adp_detail(cid, item, ccid="19000101_000001"):
    data = http.get_json(f"{ADP_BASE}/{item}", params=_adp_params(cid, ccid))
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
    try:  # some iCIMS portals only serve the framed job list after a cookie-setting first visit
        http.get_text(f"https://{host}/jobs/search", params={"ss": 1})
    except http.FetchError:
        pass
    for page in range(max_pages):
        raw = http.get_text(f"https://{host}/jobs/search", params={"ss": 1, "in_iframe": 1, "pr": page})
        redirect = re.search(r"window\.top\.location\.href\s*=\s*'([^']+)'", raw)
        if redirect and not found:  # the portal hands off to a Jibe career site
            target = redirect.group(1).replace("\\/", "/")
            p = urlparse(target)
            return fetch_jibe(f"{p.scheme}://{p.netloc}")
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
    raw = ""
    for path in ("jobs/viewall", "jobs"):
        try:
            raw = http.get_text(f"https://jobs.jobvite.com/{token}/{path}")
        except http.FetchError:
            continue
        if f"/{token}/job/" in raw:
            break
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


# ---------- Jibe (iCIMS-hosted career sites) ----------

def fetch_jibe(base, max_pages=20):
    jobs = []
    for page in range(1, max_pages + 1):
        data = http.get_json(f"{base.rstrip('/')}/api/jobs", params={"page": page, "limit": 100,
                                                                      "sortBy": "posted_date", "descending": "true"})
        items = data.get("jobs", [])
        for item in items:
            d = item.get("data", item)
            url = d.get("apply_url") or f"{base.rstrip('/')}/jobs/{d.get('slug') or d.get('req_id')}"
            loc = d.get("full_location") or d.get("location_name") or ", ".join(
                x for x in [d.get("city"), d.get("state")] if x)
            desc = html_to_text((d.get("description") or "") + " " + (d.get("qualifications") or ""))
            jobs.append(_job(d.get("title"), loc, url, remote=("remote" in (loc or "").lower()) or None,
                             posted=d.get("posted_date") or d.get("create_date"), department=d.get("department"),
                             description=desc))
        if len(items) < 100 or len(jobs) >= data.get("totalCount", 0):
            return jobs
    return jobs


# ---------- Rippling ----------

def fetch_rippling(slug):
    data = None
    for url in (f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs",
                f"https://ats.rippling.com/api/v2/board/{slug}/jobs"):
        try:
            data = http.get_json(url)
            break
        except http.FetchError:
            continue
    if data is None:
        raise http.FetchError("no Rippling job board found")
    items = data.get("items", data) if isinstance(data, dict) else data
    jobs = []
    for j in items or []:
        loc = j.get("workLocation") or j.get("locations") or ""
        if isinstance(loc, dict):
            loc = loc.get("label") or loc.get("name") or ""
        elif isinstance(loc, list):
            loc = "; ".join((x.get("name") or x.get("label") or "") if isinstance(x, dict) else str(x) for x in loc)
        url = j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{j.get('uuid') or j.get('id')}"
        dept = j.get("department")
        jobs.append(_job(j.get("name") or j.get("title"), loc, url, remote=("remote" in str(loc).lower()) or None,
                         department=dept.get("label") if isinstance(dept, dict) else dept, detail=("page", url)))
    return jobs


# ---------- UKG Ready (saashr.com) ----------

def fetch_ukgready(token):
    host, company = token.split("|")
    jobs, offset = [], 0
    while offset < 500:
        data = http.get_json(f"https://{host}/ta/rest/ui/recruitment/companies/%7C{company}/job-requisitions",
                             params={"offset": offset, "size": 50, "sort": "desc", "ein_id": "", "lang": "en-US"})
        reqs = data.get("job_requisitions", [])
        for r in reqs:
            loc = r.get("location") or {}
            loc_text = ", ".join(x for x in [loc.get("city"), loc.get("state")] if x)
            url = f"https://{host}/ta/{company}.careers?ShowJob={r.get('id')}"
            jobs.append(_job(r.get("job_title"), loc_text, url, remote=bool(r.get("is_remote_job")) or None,
                             description=html_to_text(r.get("job_description") or ""), detail=("page", url)))
        offset += 50
        if len(reqs) < 50:
            return jobs
    return jobs


# ---------- UKG Pro (UltiPro) job boards ----------

def fetch_ukgpro(token):
    host, tenant, guid = token.split("|")
    base = f"https://{host}/{tenant}/JobBoard/{guid}"
    jobs, skip = [], 0
    while skip < 1000:
        body = {"opportunitySearch": {"Top": 50, "Skip": skip, "QueryString": "",
                                      "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
                                      "Filters": [{"t": "TermsSearchFilterDto", "fieldName": n, "extra": None, "values": []}
                                                  for n in (4, 5, 6)]},
                "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                  "hasNoLicenses": False, "SkippedSkills": []}}
        data = http.post_json(f"{base}/JobBoardView/LoadSearchResults", body)
        opps = data.get("opportunities", [])
        for o in opps:
            locs = []
            for loc in o.get("Locations") or []:
                addr = loc.get("Address") or {}
                state = (addr.get("State") or {}).get("Code")
                locs.append(", ".join(x for x in [addr.get("City"), state] if x) or loc.get("LocalizedDescription") or "")
            loc_text = "; ".join(x for x in locs if x)
            url = f"{base}/OpportunityDetail?opportunityId={o.get('Id')}"
            jobs.append(_job(o.get("Title"), loc_text, url,
                             remote=("remote" in (loc_text + str(o.get("Title"))).lower()) or None,
                             posted=o.get("PostedDate"), description=html_to_text(o.get("BriefDescription") or ""),
                             detail=("page", url)))
        skip += 50
        if not opps or skip >= data.get("totalCount", 0):
            return jobs
    return jobs


# ---------- Cornerstone (csod.com) career sites ----------

def fetch_csod(token):
    corp, site = token.split("|")
    home = http.get_text(f"https://{corp}.csod.com/ux/ats/careersite/{site}/home?c={corp}")
    m = re.search(r'"token"\s*:\s*"([^"]+)"', home)
    if not m:
        raise http.FetchError("Cornerstone page had no access token")
    headers = {"Authorization": f"Bearer {m.group(1)}", "Content-Type": "application/json"}
    jobs, page = [], 1
    while page <= 10:
        body = {"careerSiteId": int(site), "careerSitePageId": int(site), "pageNumber": page, "pageSize": 100,
                "cultureId": 1, "searchText": "", "cultureName": "en-US", "states": [], "countryCodes": [],
                "cities": [], "placeID": "", "radius": None, "postingsWithinDays": None,
                "customFieldCheckboxKeys": [], "customFieldDropdowns": [], "customFieldRadios": []}
        cloud = re.search(r'"cloud"\s*:\s*"(https://[^"]+)"', home)
        api_host = cloud.group(1).rstrip("/") if cloud else "https://us.api.csod.com"
        data = http.post_json(f"{api_host}/rec-job-search/external/jobs", body, headers=headers)
        reqs = (data.get("data") or {}).get("requisitions", [])
        for r in reqs:
            locs = r.get("locations") or []
            loc_text = "; ".join(", ".join(x for x in [l.get("city"), l.get("state")] if x) for l in locs)
            rid = r.get("requisitionId")
            url = f"https://{corp}.csod.com/ux/ats/careersite/{site}/home/requisition/{rid}?c={corp}"
            jobs.append(_job(r.get("displayJobTitle"), loc_text, url, remote=("remote" in loc_text.lower()) or None,
                             posted=r.get("postingEffectiveDate"), detail=("page", url)))
        total = (data.get("data") or {}).get("totalCount", 0)
        if not reqs or page * 100 >= total:
            return jobs
        page += 1
    return jobs


# ---------- same-site posting pages (orgs that publish openings as pages on their own site) ----------

def fetch_site_links(url, limit=40):
    raw = http.get_text(url)
    base = urlparse(url)
    prefix = base.path.rstrip("/") + "/"
    jobs, seen = [], set()
    for href, label in re.findall(r"""<a\b[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", raw, re.I | re.S):
        full = urljoin(url, href.strip())
        p = urlparse(full)
        if p.netloc != base.netloc or not p.path.startswith(prefix) or p.path.rstrip("/") + "/" == prefix:
            continue
        slug = p.path[len(prefix):].strip("/")
        if not slug or "/" in slug or full in seen:
            continue
        seen.add(full)
        text = html_to_text(label)
        jobs.append(_job(text if 3 <= len(text) <= 120 else "", "", full, detail=("page", full)))
        if len(jobs) >= limit:
            break
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
    "rippling": fetch_rippling,
    "jibe": fetch_jibe,
    "ukgready": fetch_ukgready,
    "csod": fetch_csod,
    "ukgpro": fetch_ukgpro,
    "site_links": fetch_site_links,
}
