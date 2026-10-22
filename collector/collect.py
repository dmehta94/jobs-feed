"""Weekly job collection.

Reads orgs.csv, finds each org's hiring platform, pulls every open posting, keeps the
ones that are plausibly relevant, and writes:

    data/jobs.json       relevant open postings, with first-seen dates
    data/health.json     per-source status, so broken sources are easy to spot
    data/platforms.json  detected hiring platforms, reused on later runs
    data/seen.json       first-seen dates by posting URL

Run from the repo root:  python -m collector.collect
"""
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import http
from . import discovery, sources_extra
from .government import fetch_nyc, fetch_usajobs
from .sources import ADAPTERS, detect, fetch_detail, probe
from .fit import title_priority
from .text import (advanced_degree_required, extract_salary, location_is_ok, remote_from_text, title_is_relevant,
                   years_required)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DESCRIPTION_CHARS = 12000
MAX_DETAIL_FETCHES_PER_ORG = 80
MAX_REMOTE_CHECKS_PER_ORG = 80  # out-of-area postings whose description is read to see if they're remote
SEEN_RETENTION_DAYS = 90


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def load_orgs(path=ROOT / "orgs.csv"):
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("name", "").strip()]


def resolve_platform(org, cache):
    """Explicit platform in orgs.csv > cached detection > fresh detection > name probe."""
    if org.get("ats") and org.get("token"):
        return org["ats"].strip(), org["token"].strip(), "orgs.csv"
    cached = cache.get(org["name"])
    if cached and cached.get("platform") in ADAPTERS:
        return cached["platform"], cached["token"], "cached"
    platform, token, how = None, None, ""
    try:
        platform, token, how = detect(org.get("careers_url"))
    except http.FetchError as exc:
        how = f"careers page failed ({exc})"
    if platform in ADAPTERS:
        return platform, token, how
    p2, t2 = probe(org["name"], org.get("careers_url"))
    if p2:
        return p2, t2, "matched by name"
    return platform, None, how


def finish(job, *, org, category, tier, source, today, seen):
    desc = job.get("description") or ""
    job_id = hashlib.sha1((job.get("url") or org + job.get("title", "")).encode()).hexdigest()[:12]
    first_seen = seen.get(job["url"], {}).get("first_seen", today)
    seen[job["url"]] = {"first_seen": first_seen, "last_seen": today}
    out = {
        "id": job_id,
        "org": job.get("org") or org,
        "category": category,
        "tier": tier,
        "source": source,
        "title": job["title"],
        "location": job.get("location"),
        "remote": job.get("remote"),
        "url": job.get("url"),
        "posted": job.get("posted"),
        "first_seen": first_seen,
        "new_this_week": first_seen >= (date.fromisoformat(today) - timedelta(days=7)).isoformat(),
        "employment_type": job.get("employment_type"),
        "department": job.get("department"),
        "salary_min": job.get("salary_min"),
        "salary_max": job.get("salary_max"),
        "salary_text": job.get("salary_text"),
        "years_required": years_required(desc),
        "advanced_degree_required": (degree := advanced_degree_required(desc)),  # "PhD", "Master's", or None
        "phd_required": degree == "PhD",
        "description": desc[:DESCRIPTION_CHARS],
    }
    if job.get("extra"):
        out.update(job["extra"])
    return out


def _clearly_abroad(location):
    return not location_is_ok(location, True)


def _read_detail(job):
    try:
        job["description"] = fetch_detail(job["_detail"])
    except http.FetchError:
        return
    if job["description"] and job.get("salary_min") is None:
        job["salary_min"], job["salary_max"], job["salary_text"] = extract_salary(job["description"])


def collect_org(org, cache, today, seen):
    name = org["name"]
    tier = int(org.get("tier") or 2)
    health = {"org": name, "category": org.get("category"), "tier": tier}
    platform, token, how = resolve_platform(org, cache)
    health.update(platform=platform, token=token, found_by=how)
    if platform not in ADAPTERS:
        health.update(status="no supported platform found", total=0, relevant=0)
        return health, []
    try:
        raw = ADAPTERS[platform](token)
    except http.FetchError as exc:
        cache.pop(name, None)  # re-detect next week
        health.update(status=f"fetch failed: {exc}", total=0, relevant=0)
        return health, []
    if not raw and how != "orgs.csv":
        # An empty board found by guessing is usually the wrong board (a same-named account). Re-detect next run.
        cache.pop(name, None)
        health.update(status="empty: guessed board had no postings", total=0, relevant=0)
        return health, []
    cache[name] = {"platform": platform, "token": token}
    # Postings found only as links have no title yet; read those pages first.
    untitled = 0
    for job in raw:
        if not job["title"] and job.get("_detail") and job["_detail"][0] == "page" and untitled < MAX_DETAIL_FETCHES_PER_ORG:
            untitled += 1
            try:
                job["title"], job["description"] = sources_extra.page_detail(job["_detail"][1])
            except http.FetchError:
                pass
    raw = [j for j in raw if j["title"]]
    for j in raw:  # many boards flag remote work only in the title, e.g. "Research Analyst (Remote Eligible)"
        if not j.get("remote") and re.search(r"\bremote\b", j["title"], re.I):
            j["remote"] = True
    titled = [j for j in raw if title_is_relevant(j["title"])]
    relevant = [j for j in titled if location_is_ok(j["location"], j["remote"])]
    # Postings based elsewhere often say only in the description that they can be done remotely.
    elsewhere = sorted((j for j in titled if not location_is_ok(j["location"], j["remote"])
                        and not _clearly_abroad(j["location"])),
                       key=lambda j: title_priority(j["title"]))
    checked = 0
    for job in elsewhere:
        if checked >= MAX_REMOTE_CHECKS_PER_ORG or title_priority(job["title"]) > 0:
            break
        if not job.get("description") and job.get("_detail"):
            checked += 1
            _read_detail(job)
        if remote_from_text(job.get("description")):
            job["remote"] = True
            relevant.append(job)
    relevant.sort(key=lambda j: title_priority(j["title"]))
    details = 0
    for job in relevant:
        if not job.get("description") and job.get("_detail") and details < MAX_DETAIL_FETCHES_PER_ORG:
            details += 1
            _read_detail(job)
        if not job.get("remote") and remote_from_text(job.get("description")):
            job["remote"] = True
    jobs = [finish(j, org=name, category=org.get("category"), tier=tier, source=platform,
                   today=today, seen=seen) for j in relevant]
    health.update(status="ok", total=len(raw), relevant=len(jobs))
    return health, jobs


def collect_government(today, seen):
    results = []
    try:
        total, nyc = fetch_nyc()
        jobs = [finish(j, org=j["org"], category="NYC Government", tier=2, source="nyc_open_data",
                       today=today, seen=seen) for j in nyc]
        results.append(({"org": "NYC Jobs (NYC Open Data)", "category": "NYC Government", "status": "ok",
                         "total": total, "relevant": len(jobs)}, jobs))
    except http.FetchError as exc:
        results.append(({"org": "NYC Jobs (NYC Open Data)", "status": f"fetch failed: {exc}"}, []))
    try:
        total, fed = fetch_usajobs()
        if total is None:
            results.append(({"org": "USAJOBS", "status": "skipped (no API key set)"}, []))
        else:
            fed = [j for j in fed if title_is_relevant(j["title"])]
            jobs = [finish(j, org=j["org"], category="Federal Government", tier=3, source="usajobs",
                           today=today, seen=seen) for j in fed]
            results.append(({"org": "USAJOBS", "status": "ok", "total": total, "relevant": len(jobs)}, jobs))
    except http.FetchError as exc:
        results.append(({"org": "USAJOBS", "status": f"fetch failed: {exc}"}, []))
    return results


def _norm_key(org, title):
    return (re.sub(r"[^a-z0-9]", "", (org or "").lower()), re.sub(r"[^a-z0-9]", "", (title or "").lower()))


def collect_discovery(today, seen, known_jobs):
    """Keyword searches across aggregators. Postings already found on an org's own board are skipped."""
    known = {_norm_key(j["org"], j["title"]) for j in known_jobs}
    results = []
    for name, fetch in discovery.SOURCES:
        try:
            raw = fetch()
        except http.FetchError as exc:
            results.append(({"org": name, "category": "Discovered", "status": f"fetch failed: {exc}"}, []))
            continue
        if raw is None:
            results.append(({"org": name, "category": "Discovered", "status": "skipped (no API key set)"}, []))
            continue
        kept = [j for j in raw if discovery.keep(j) and discovery.recent_enough(j)
                and _norm_key(j["org"], j["title"]) not in known]
        jobs = [finish(j, org=j["org"], category="Discovered", tier=3, source=name.lower().replace(" ", ""),
                       today=today, seen=seen) for j in kept]
        known.update(_norm_key(j["org"], j["title"]) for j in jobs)
        results.append(({"org": name, "category": "Discovered", "status": "ok", "total": len(raw),
                         "relevant": len(jobs)}, jobs))
    return results


def main():
    today = date.today().isoformat()
    started = time.time()
    orgs = load_orgs()
    cache = load_json(DATA / "platforms.json", {})
    seen = load_json(DATA / "seen.json", {})
    first_run = not seen

    health, jobs = [], []
    for org in orgs:
        h, j = collect_org(org, cache, today, seen)
        print(f"{h['org'][:45]:45} {str(h.get('platform')):22} {h['status'][:40]:40} {h.get('relevant', 0):>4}/{h.get('total', 0)}")
        health.append(h)
        jobs.extend(j)
    for h, j in collect_government(today, seen):
        print(f"{h['org'][:45]:45} {'':22} {h['status'][:40]:40} {h.get('relevant', 0):>4}/{h.get('total', 0)}")
        health.append(h)
        jobs.extend(j)
    for h, j in collect_discovery(today, seen, jobs):
        print(f"{h['org'][:45]:45} {'(search)':22} {h['status'][:40]:40} {h.get('relevant', 0):>4}/{h.get('total', 0)}")
        health.append(h)
        jobs.extend(j)

    # De-duplicate (some orgs share a board) and sort: new first, then tier.
    unique = {}
    for job in jobs:
        unique.setdefault(job["url"], job)
    jobs = sorted(unique.values(), key=lambda j: (not j["new_this_week"], j["tier"], j["org"], j["title"]))

    cutoff = (date.today() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    seen = {url: v for url, v in seen.items() if v.get("last_seen", today) >= cutoff}

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = sum(1 for h in health if h.get("status") == "ok")
    write_json(DATA / "jobs.json", {
        "generated_at": generated,
        "first_run": first_run,  # on the first run every posting looks new; use 'posted' instead
        "sources_ok": ok,
        "sources_total": len(health),
        "job_count": len(jobs),
        "new_this_week": sum(j["new_this_week"] for j in jobs),
        "jobs": jobs,
    })
    write_json(DATA / "health.json", {"generated_at": generated, "sources": health})
    write_json(DATA / "platforms.json", dict(sorted(cache.items())))
    write_json(DATA / "seen.json", dict(sorted(seen.items())))
    print(f"\n{len(jobs)} relevant postings ({sum(j['new_this_week'] for j in jobs)} new) "
          f"from {ok}/{len(health)} working sources in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
