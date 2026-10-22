"""Diagnostics for sources that failed: find working careers pages and capture hiring-platform pages.

For every org whose last status wasn't "ok", tries the configured careers URL plus common
alternatives on the org's domain, records what each returned, which hiring platforms the pages
reference, and saves the first part of each hiring-platform page so readers can be written
against real responses. Output goes to probe/ (committed to the `probe` branch by the workflow).

Run from the repo root:  python -m collector.probe
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import http
from .collect import load_orgs, load_json
from .sources import UNSUPPORTED, detect_in_html

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "probe"
PATHS = ["", "/careers", "/careers/", "/jobs", "/jobs/", "/about/careers", "/about-us/careers", "/join-us",
         "/work-with-us", "/employment", "/opportunities", "/about/jobs", "/careers/open-positions",
         "/about/work-with-us", "/company/careers"]
ATS_HOST = re.compile(
    r"https?://[^\s\"'<>]*(greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|workable\.com|"
    r"myworkdayjobs\.com|bamboohr\.com|recruitee\.com|breezy\.hr|paylocity\.com|ultipro\.com|ukg\.net|"
    r"icims\.com|adp\.com|taleo\.net|applytojob\.com|paycomonline\.net|dayforcehcm\.com|successfactors\.|"
    r"oraclecloud\.com|jobvite\.com|interfolio\.com|hirebridge\.com|applicantpro\.com|isolvedhire\.com|"
    r"careerplug\.com|rippling\.com|dover\.com|teamtailor\.com|personio\.|pinpointhq\.com|"
    r"governmentjobs\.com|peopleadmin\.com|silkroad\.com|clearcompany\.com|hrmdirect\.com|"
    r"theapplicantmanager\.com|appone\.com|zohorecruit\.com|freshteam\.com|jazzhr\.com|"
    r"workforcenow\.adp\.com|myjobs\.adp\.com|recruiting\.adp\.com|idealist\.org)[^\s\"'<>]*", re.I)
SNIPPET = 150_000


def _safe(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:60]


def try_url(url):
    try:
        resp = http._request("GET", url, retries=0, timeout=20, allow_redirects=True)
        return {"url": url, "status": resp.status_code, "final_url": resp.url, "text": resp.text}
    except http.FetchError as exc:
        return {"url": url, "status": str(exc), "final_url": None, "text": ""}


def probe_org(org):
    careers = org.get("careers_url") or ""
    parsed = urlparse(careers)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    candidates = [careers] + [base + p for p in PATHS if base]
    seen, attempts, ats_links = set(), [], set()
    best = None
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        r = try_url(url)
        page = r.pop("text")
        platform, token = detect_in_html(page) if page else (None, None)
        links = sorted(set(m.group(0).rstrip("\\);,.") for m in ATS_HOST.finditer(page)))[:25]
        ats_links.update(links)
        unsupported = [name for name, rx in UNSUPPORTED if rx.search(page)] if page else []
        attempts.append({**r, "platform": platform, "token": token, "ats_links": links,
                         "unsupported": unsupported, "length": len(page)})
        if r["status"] == 200 and best is None and (platform or links or "career" in url or "job" in url):
            best = r["final_url"] or url
        if platform and not platform.startswith("unsupported"):
            break
    # Save raw hiring-platform pages so readers can be written against real responses.
    saved = []
    for i, link in enumerate(sorted(ats_links)[:4]):
        r = try_url(link)
        if r["text"]:
            path = OUT / "pages" / f"{_safe(org['name'])}__{i}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r["text"][:SNIPPET])
            saved.append({"link": link, "status": r["status"], "final_url": r["final_url"], "file": str(path.relative_to(ROOT))})
    return {"org": org["name"], "careers_url": careers, "working_page": best,
            "attempts": attempts, "ats_links": sorted(ats_links), "saved_pages": saved}


def probe_aggregators():
    """Record why aggregator results were filtered out."""
    from . import discovery
    from .text import location_is_ok, title_is_relevant
    rows = []
    for name, fetch in (("The Muse", lambda: discovery.fetch_themuse(max_pages=3)), ("Remotive", discovery.fetch_remotive)):
        try:
            jobs = fetch() or []
        except http.FetchError as exc:
            rows.append({"source": name, "error": str(exc)})
            continue
        for j in jobs:
            rows.append({"source": name, "org": j["org"], "title": j["title"], "location": j["location"],
                         "title_ok": title_is_relevant(j["title"]),
                         "location_ok": location_is_ok(j["location"], j["remote"]),
                         "mission_ok": discovery.has_mission(j), "desc_len": len(j.get("description") or "")})
    (OUT / "aggregators.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"Aggregator rows: {len(rows)}")


LIST_URLS = {
    "icims": lambda t: [f"https://{t}/jobs/search?ss=1&in_iframe=1", f"https://{t}/jobs/search?ss=1"],
    "jobvite": lambda t: [f"https://jobs.jobvite.com/{t}/jobs", f"https://jobs.jobvite.com/{t}/search"],
    "jazzhr": lambda t: [f"https://{t}.applytojob.com/apply", f"https://{t}.applytojob.com/apply/jobs"],
    "adp": lambda t: [("https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/"
                       f"job-requisitions?cid={t}&lang=en_US&ccId=19000101_000001&locale=en_US&$top=20")],
    "talentbrew": lambda t: [t, t.rstrip("/") + "/search-jobs"],
    "ukgready": lambda t: [f"https://{t.split('|')[0]}/ta/rest/ui/recruitment/companies/%7C{t.split('|')[1]}/job-requisitions?offset=0&size=20&sort=desc&ein_id=&lang=en-US",
                           f"https://{t.split('|')[0]}/ta/{t.split('|')[1]}.careers?CareersSearch"],
    "jibe": lambda t: [t.rstrip('/') + "/api/jobs?page=1&limit=5", t.rstrip('/') + "/jobs"],
    "greenhouse": lambda t: [f"https://job-boards.greenhouse.io/{t}", f"https://job-boards.eu.greenhouse.io/{t}",
                             f"https://boards-api.eu.greenhouse.io/v1/boards/{t}/jobs", f"https://boards.greenhouse.io/{t}"],
}
_SRC = re.compile(r"""<(?:script|iframe)[^>]+src=["']([^"']+)["']""", re.I)


def deep_probe(org):
    """Save full careers pages (scripts and iframes listed) and raw list-endpoint responses."""
    out = {"org": org["name"], "pages": []}
    urls = [org.get("careers_url")]
    if org.get("ats") in LIST_URLS and org.get("token"):
        urls += LIST_URLS[org["ats"]](org["token"])
    for i, url in enumerate(u for u in urls if u):
        r = try_url(url)
        raw = r.pop("text")
        fname = OUT / "deep" / f"{_safe(org['name'])}__{i}.html"
        fname.parent.mkdir(parents=True, exist_ok=True)
        fname.write_text(raw[:400_000])
        out["pages"].append({**r, "file": str(fname.relative_to(ROOT)), "length": len(raw),
                             "srcs": sorted(set(_SRC.findall(raw)))[:60]})
    return out


def main(names=None):
    health = load_json(ROOT / "data" / "health.json", {}).get("sources", [])
    failing = {h["org"] for h in health if h.get("status") != "ok"}
    orgs = [o for o in load_orgs() if (o["name"] in names if names else o["name"] in failing)]
    results = []
    for org in orgs:
        res = probe_org(org)
        results.append(res)
        found = res["attempts"][-1]["platform"] if res["attempts"] else None
        print(f"{org['name'][:45]:45} working={str(res['working_page'])[:60]:60} ats_links={len(res['ats_links'])} last={found}")
    OUT.mkdir(exist_ok=True)
    (OUT / "probe.json").write_text(json.dumps(results, indent=2) + "\n")
    if not names:
        probe_aggregators()
    print(f"Probed {len(results)} orgs.")
    return 0


def main_deep(names):
    orgs = [o for o in load_orgs() if o["name"] in names]
    results = [deep_probe(o) for o in orgs]
    OUT.mkdir(exist_ok=True)
    (OUT / "deep.json").write_text(json.dumps(results, indent=2) + "\n")
    for r in results:
        print(r["org"], [(p["status"], p["length"]) for p in r["pages"]])
    return 0


def main_test(names):
    """Run the real collection for a few orgs and record counts, samples, and errors."""
    import traceback
    from .collect import collect_org
    orgs = [o for o in load_orgs() if o["name"] in names]
    results = []
    for org in orgs:
        try:
            health, jobs = collect_org(org, {}, "2026-01-01", {})
            from .sources import ADAPTERS
            raw_sample = []
            if health.get("platform") in ADAPTERS:
                try:
                    raw_sample = [{"title": j["title"], "location": j["location"], "remote": j["remote"]}
                                  for j in ADAPTERS[health["platform"]](health["token"])[:12]]
                except Exception as exc:
                    raw_sample = [{"error": str(exc)}]
            res = {"org": org["name"], "raw_sample": raw_sample,
                   **{k: health.get(k) for k in ("platform", "token", "found_by", "status", "total", "relevant")},
                   "samples": [{"title": j["title"], "location": j["location"], "url": j["url"],
                                "desc_len": len(j["description"] or "")} for j in jobs[:5]]}
        except Exception as exc:  # report, don't stop
            res = {"org": org["name"], "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
        results.append(res)
        print(json.dumps({k: v for k, v in res.items() if k != "trace"})[:400])
    OUT.mkdir(exist_ok=True)
    (OUT / "test.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0


def main_urls(urls):
    """Fetch arbitrary URLs and save the responses, to check what a runner can reach."""
    rows = []
    for i, url in enumerate(urls):
        try:
            resp = http.session.get(url, timeout=30, allow_redirects=True)
            status, final, body, ctype = resp.status_code, resp.url, resp.text, resp.headers.get("content-type", "")
        except Exception as exc:  # report, don't stop
            status, final, body, ctype = f"{type(exc).__name__}: {exc}"[:200], None, "", ""
        fname = OUT / "urls" / f"{i:02d}_{_safe(urlparse(url).netloc + urlparse(url).path)[:50]}.txt"
        fname.parent.mkdir(parents=True, exist_ok=True)
        fname.write_text(body[:400_000])
        rows.append({"url": url, "status": status, "final_url": final, "content_type": ctype,
                     "length": len(body), "file": str(fname.relative_to(ROOT)), "head": body[:300]})
        print(status, len(body), url)
    OUT.mkdir(exist_ok=True)
    (OUT / "urls.json").write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--urls":
        sys.exit(main_urls(args[1:]))
    if args and args[0] == "--deep":
        sys.exit(main_deep(args[1:]))
    if args and args[0] == "--test":
        sys.exit(main_test(args[1:]))
    sys.exit(main(args or None))
