"""Temporary diagnostic: which request shape gets past Workday's HTTP 429."""
import json, time
from pathlib import Path
import requests

TOKEN = "urban.wd115.myworkdayjobs.com|urban|Urban-Careers"
host, tenant, site = TOKEN.split("|")
api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
page = f"https://{host}/{site}"
BODY = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "data"}
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
out = []

def rec(name, r):
    out.append({"variant": name, "status": r.status_code, "retry_after": r.headers.get("Retry-After"),
                "ctype": r.headers.get("Content-Type"), "server": r.headers.get("Server"),
                "body": r.text[:300]})

s = requests.Session(); s.headers["User-Agent"] = "jobs-feed/1.0 (weekly job collector; +https://github.com/dmehta94)"
rec("current", s.post(api, json=BODY, timeout=25)); time.sleep(3)
s2 = requests.Session(); s2.headers.update({"User-Agent": BROWSER, "Accept": "application/json", "Content-Type": "application/json",
                                            "Accept-Language": "en-US", "Origin": f"https://{host}", "Referer": page})
rec("browser headers", s2.post(api, data=json.dumps(BODY), timeout=25)); time.sleep(3)
s3 = requests.Session(); s3.headers.update(s2.headers)
rec("warm page GET", s3.get(page, timeout=25)); time.sleep(2)
rec("after warm", s3.post(api, data=json.dumps(BODY), timeout=25)); time.sleep(3)
s4 = requests.Session(); s4.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
rec("json headers, default UA", s4.post(api, data=json.dumps(BODY), timeout=25))
Path("probe").mkdir(exist_ok=True); Path("probe/diag.json").write_text(json.dumps(out, indent=2))
for o in out: print(o["variant"], o["status"], o["retry_after"], o["body"][:120].replace("\n", " "))
