"""Hiring-platform detection and one adapter per platform.

Each adapter takes a platform token and returns a list of raw job dicts with the
keys: title, location, remote, url, posted, department, employment_type,
salary_min, salary_max, salary_text, description.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from . import http, sources_extra
from .text import extract_salary, html_to_text

# ---------- detection ----------

# Order matters: the first platform found on a page wins.
PATTERNS = [
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?!embed)([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/([A-Za-z0-9_.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/(?!api)([A-Za-z0-9_-]+)")),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
    ("bamboohr", re.compile(r"([a-z0-9-]+)\.bamboohr\.com")),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com")),
    ("breezy", re.compile(r"([a-z0-9-]+)\.breezy\.hr")),
] + sources_extra.PATTERNS

# Platforms we can recognize but not read yet. Reported in health.json so they can be added later.
UNSUPPORTED = [
    ("ukg", re.compile(r"recruiting\d*\.ultipro\.com|\.ukg\.net", re.I)),
    ("taleo", re.compile(r"\.taleo\.net", re.I)),
    ("paycom", re.compile(r"paycomonline\.net", re.I)),
    ("dayforce", re.compile(r"dayforcehcm\.com", re.I)),
    ("successfactors", re.compile(r"successfactors\.com|jobs\.sap\.com", re.I)),
    ("interfolio", re.compile(r"apply\.interfolio\.com", re.I)),
]

IGNORED_TOKENS = {"embed", "v1", "js", "api", "jobs", "careers", "www", "app", "cdn", "assets", "static", "help", "support"}


def detect_in_html(page):
    """Return (platform, token) for the first supported platform referenced in the page,
    ("unsupported:<name>", None) for a recognized but unsupported one, or (None, None)."""
    for platform, pattern in PATTERNS:
        for match in pattern.finditer(page):
            if platform == "workday":
                tenant, wd, site = match.groups()
                return "workday", f"{tenant}.{wd}.myworkdayjobs.com|{tenant}|{site}"
            if platform == "oracle":
                host, site = match.groups()
                return "oracle", f"{host.lower()}|{site}"
            if platform in sources_extra.MULTI_GROUP and len(match.groups()) > 1:
                return platform, "|".join(g for g in match.groups() if g)
            token = match.group(1).strip(".")
            if token.lower() not in IGNORED_TOKENS:
                return platform, token
    for name, pattern in UNSUPPORTED:
        if pattern.search(page):
            return f"unsupported:{name}", None
    return None, None


_LINK = re.compile(r"""<a\b[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_JOBBY = re.compile(r"job|career|opening|position|vacanc|join|work[- ]with|hiring|opportunit", re.I)


def candidate_job_pages(page, base_url, limit=6):
    """Same-site links on a careers page that probably lead to the actual job list."""
    base_host = urlparse(base_url).netloc.split(":")[0].removeprefix("www.")
    seen, out = set(), []
    for href, label in _LINK.findall(page):
        url = urljoin(base_url, href.strip())
        host = urlparse(url).netloc.split(":")[0].removeprefix("www.")
        if not url.startswith("http") or url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        same_site = host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)
        if same_site and (_JOBBY.search(href) or _JOBBY.search(html_to_text(label))):
            seen.add(url)
            out.append(url)
        if len(out) >= limit:
            break
    return out


def detect(careers_url):
    """Find an org's hiring platform from its careers page, following a few job-ish links.
    Falls back to reading postings linked directly from the page. Returns (platform, token, how)."""
    if not careers_url:
        return None, None, "no careers URL"
    page = http.get_text(careers_url)
    platform, token = detect_in_html(page)
    if platform and not platform.startswith("unsupported"):
        return platform, token, "careers page"
    unsupported = platform
    pages = [(careers_url, page)]
    for url in candidate_job_pages(page, careers_url):
        try:
            sub = http.get_text(url)
        except http.FetchError:
            continue
        pages.append((url, sub))
        p2, t2 = detect_in_html(sub)
        if p2 and not p2.startswith("unsupported"):
            return p2, t2, f"linked page {url}"
        unsupported = unsupported or p2
    for url, raw in pages:
        if sources_extra.has_job_links(raw):
            return "links", url, f"posting links on {url}"
    return unsupported, None, "not found on careers page"


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


GENERIC_NAME_WORDS = {"inc", "llc", "org", "the", "foundation", "institute", "corp", "corporation",
                      "company", "pbc", "nonprofit", "education", "learning", "innovation", "partners",
                      "team", "careers", "jobs", "for", "of", "and", "co", "group"}


def _words(name):
    return set(re.sub(r"\(.*?\)", "", name).lower().replace("&", "and").replace(".", "").split()) 


def names_match(org_name, board_name):
    """True when the two names differ only by generic words like 'Inc' or 'Foundation'."""
    a = {re.sub(r"[^a-z0-9]", "", w) for w in _words(org_name)} - {""}
    b = {re.sub(r"[^a-z0-9]", "", w) for w in _words(board_name)} - {""}
    if not a or not b:
        return False
    return not ((a ^ b) - GENERIC_NAME_WORDS)


def slug_candidates(name, careers_url):
    words = re.sub(r"\(.*?\)", "", name).replace("&", "and")
    words = re.sub(r"[^A-Za-z0-9 ]", "", words).lower().split()
    cands = ["".join(words), "-".join(words)]
    if careers_url:
        host = urlparse(careers_url).netloc.lower().removeprefix("www.")
        parts = host.split(".")
        if len(parts) >= 2:
            cands.append(parts[-2])
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def probe(name, careers_url):
    """Guess a Greenhouse board from the org's name. Only accepted when the board's
    own name matches the org, to avoid picking up a different company's board."""
    for slug in slug_candidates(name, careers_url):
        try:
            info = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}", retries=0)
        except http.FetchError:
            info = None
        if info and names_match(name, info.get("name", "")):
            return "greenhouse", slug
        try:  # Workable's widget endpoint reports the account name, so it can be checked the same way
            info = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", retries=0)
        except http.FetchError:
            info = None
        if info and names_match(name, info.get("name", "")):
            return "workable", slug
    return None, None


# ---------- adapters ----------

def _iso_from_ms(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _iso(value):
    if not value:
        return None
    return str(value)[:10]


def _job(title, location, url, *, remote=None, posted=None, department=None,
         employment_type=None, description="", salary_min=None, salary_max=None, salary_text=None,
         detail=None):
    if salary_min is None:
        salary_min, salary_max, salary_text = extract_salary(description)
    return {
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "remote": remote,
        "url": url,
        "posted": posted,
        "department": department,
        "employment_type": employment_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_text": salary_text,
        "description": description,
        "_detail": detail,  # (platform, args) for fetching the full description later
    }


def fetch_greenhouse(token):
    try:
        data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    except http.FetchError as exc:
        if "404" not in str(exc):
            raise
        return sources_extra.fetch_greenhouse_html(token)  # some boards turn the public API off
    jobs = []
    for j in data.get("jobs", []):
        desc = html_to_text(j.get("content", ""))
        loc = (j.get("location") or {}).get("name", "")
        depts = ", ".join(d.get("name", "") for d in j.get("departments", []) if d.get("name"))
        jobs.append(_job(j.get("title"), loc, j.get("absolute_url"),
                         remote=("remote" in loc.lower()) or None,
                         posted=_iso(j.get("first_published") or j.get("updated_at")),
                         department=depts or None, description=desc))
    return jobs


def fetch_lever(token):
    data = http.get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        desc = j.get("descriptionPlain") or html_to_text(j.get("description", ""))
        for block in j.get("lists", []):
            desc += " " + block.get("text", "") + ": " + html_to_text(block.get("content", ""))
        sal = j.get("salaryRange") or {}
        smin = smax = stext = None
        if sal.get("min") and str(sal.get("interval", "")).startswith("per-year"):
            smin, smax = float(sal["min"]), float(sal.get("max") or sal["min"])
            stext = f"{sal.get('currency', '')} {sal['min']}–{sal.get('max')} {sal.get('interval')}"
        jobs.append(_job(j.get("text"), cats.get("location") or ", ".join(cats.get("allLocations", [])),
                         j.get("hostedUrl"), remote=(j.get("workplaceType") == "remote") or None,
                         posted=_iso_from_ms(j.get("createdAt")), department=cats.get("team"),
                         employment_type=cats.get("commitment"), description=desc,
                         salary_min=smin, salary_max=smax, salary_text=stext))
    return jobs


def fetch_ashby(token):
    data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
        comp = j.get("compensation") or {}
        stext = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary")
        smin = smax = None
        for comp_part in comp.get("summaryComponents", []) or []:
            if comp_part.get("compensationType") == "Salary" and comp_part.get("interval", "").endswith("YEAR"):
                smin, smax = comp_part.get("minValue"), comp_part.get("maxValue")
        if smin is None and stext:
            smin, smax, _ = extract_salary(stext)
        jobs.append(_job(j.get("title"), j.get("location"), j.get("jobUrl"),
                         remote=j.get("isRemote") or (j.get("workplaceType") == "Remote") or None,
                         posted=_iso(j.get("publishedAt")), department=j.get("department"),
                         employment_type=j.get("employmentType"), description=desc,
                         salary_min=smin, salary_max=smax, salary_text=stext))
    return jobs


def fetch_smartrecruiters(token):
    jobs, offset = [], 0
    while True:
        data = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}")
        for j in data.get("content", []):
            loc = j.get("location") or {}
            loc_text = ", ".join(x for x in [loc.get("city"), loc.get("region")] if x)
            jobs.append(_job(j.get("name"), loc_text,
                             f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                             remote=loc.get("remote") or None, posted=_iso(j.get("releasedDate")),
                             department=(j.get("department") or {}).get("label"),
                             employment_type=(j.get("typeOfEmployment") or {}).get("label"),
                             detail=("smartrecruiters", (token, j.get("id")))))
        offset += 100
        if offset >= data.get("totalFound", 0) or offset >= 1000:
            return jobs


def fetch_workable(token):
    data = http.post_json(f"https://apply.workable.com/api/v3/accounts/{token}/jobs", {})
    jobs = []
    for j in data.get("results", []):
        loc = j.get("location") or {}
        loc_text = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
        jobs.append(_job(j.get("title"), loc_text, f"https://apply.workable.com/{token}/j/{j.get('shortcode')}/",
                         remote=j.get("remote") or None, posted=_iso(j.get("published")),
                         department=", ".join(j.get("department") or []) or None,
                         employment_type=j.get("type"), detail=("workable", (token, j.get("shortcode")))))
    return jobs


WORKDAY_SEARCH_TERMS = ["data", "research", "analyst", "efficacy", "psychometric", "learning science", "evaluation"]


def fetch_workday(token):
    host, tenant, site = token.split("|")
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    found = {}
    for term in WORKDAY_SEARCH_TERMS:
        offset = 0
        while offset < 200:
            data = http.post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20,
                                                   "offset": offset, "searchText": term})
            postings = data.get("jobPostings", [])
            for j in postings:
                path = j.get("externalPath")
                if path and path not in found:
                    found[path] = _job(j.get("title"), j.get("locationsText"),
                                       f"https://{host}/{site}{path}",
                                       remote=("remote" in (j.get("locationsText") or "").lower()) or None,
                                       posted=_workday_posted(j.get("postedOn")),
                                       detail=("workday", (base, path)))
            offset += 20
            if len(postings) < 20:
                break
    return list(found.values())


def _workday_posted(text):
    """Workday gives relative dates ('Posted 3 Days Ago'); turn them into an ISO date."""
    if not text:
        return None
    from datetime import date, timedelta
    t = text.lower()
    if "today" in t:
        return date.today().isoformat()
    if "yesterday" in t:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", t)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)))).isoformat()
    return None


def fetch_bamboohr(token):
    data = http.get_json(f"https://{token}.bamboohr.com/careers/list", headers={"Accept": "application/json"})
    jobs = []
    for j in data.get("result", []):
        loc = j.get("location") or {}
        loc_text = ", ".join(x for x in [loc.get("city"), loc.get("state")] if x)
        jobs.append(_job(j.get("jobOpeningName"), loc_text, f"https://{token}.bamboohr.com/careers/{j.get('id')}",
                         remote=bool(j.get("isRemote")) or None, department=j.get("departmentLabel"),
                         employment_type=j.get("employmentStatusLabel"),
                         detail=("bamboohr", (token, j.get("id")))))
    return jobs


def fetch_recruitee(token):
    data = http.get_json(f"https://{token}.recruitee.com/api/offers/")
    jobs = []
    for j in data.get("offers", []):
        desc = html_to_text((j.get("description") or "") + " " + (j.get("requirements") or ""))
        jobs.append(_job(j.get("title"), j.get("location"), j.get("careers_url"),
                         remote=j.get("remote") or None, posted=_iso(j.get("published_at")),
                         department=j.get("department"), employment_type=j.get("employment_type_code"),
                         description=desc))
    return jobs


def fetch_breezy(token):
    data = http.get_json(f"https://{token}.breezy.hr/json")
    jobs = []
    for j in data:
        loc = j.get("location") or {}
        jobs.append(_job(j.get("name"), loc.get("name"), j.get("url"),
                         remote=loc.get("is_remote") or None, posted=_iso(j.get("published_date")),
                         department=j.get("department"), employment_type=(j.get("type") or {}).get("name")))
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "workday": fetch_workday,
    "bamboohr": fetch_bamboohr,
    "recruitee": fetch_recruitee,
    "breezy": fetch_breezy,
    **sources_extra.ADAPTERS,
}


# ---------- full descriptions for platforms whose list endpoint omits them ----------

def fetch_detail(detail):
    """Return the plain-text description for a job whose list entry had none."""
    platform, args = detail
    if platform == "page":
        return sources_extra.page_detail(args)[1]
    if platform == "adp":
        return sources_extra.adp_detail(*args)
    if platform == "oracle":
        return sources_extra.oracle_detail(*args)
    if platform == "workday":
        base, path = args
        info = http.get_json(f"{base}{path}").get("jobPostingInfo", {})
        return html_to_text(info.get("jobDescription", ""))
    if platform == "smartrecruiters":
        token, job_id = args
        sections = (http.get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{job_id}")
                    .get("jobAd", {}).get("sections", {}))
        return html_to_text(" ".join((v or {}).get("text", "") for v in sections.values()))
    if platform == "workable":
        token, code = args
        data = http.get_json(f"https://apply.workable.com/api/v2/accounts/{token}/jobs/{code}")
        return html_to_text(" ".join(data.get(k, "") or "" for k in ("description", "requirements", "benefits")))
    if platform == "bamboohr":
        token, job_id = args
        data = http.get_json(f"https://{token}.bamboohr.com/careers/{job_id}/detail",
                             headers={"Accept": "application/json"})
        return html_to_text(((data.get("result") or {}).get("jobOpening") or {}).get("description", ""))
    return ""
