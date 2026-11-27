"""Temporary diagnostic: Workday 429 across sites."""
import json, re, time
from pathlib import Path
import requests
from .sources import workday_headers, WORKDAY_UA

TOKENS = ["tntp.wd5.myworkdayjobs.com|tntp|TNTP", "collegeboard.wd1.myworkdayjobs.com|collegeboard|Careers",
          "rand.wd5.myworkdayjobs.com|rand|External_Career_Site", "gatesfoundation.wd1.myworkdayjobs.com|gatesfoundation|Gates",
          "nyp.wd1.myworkdayjobs.com|nyp|nypcareers", "msk.wd108.myworkdayjobs.com|msk|MSKCC_Careers_Primary",
          "finra.wd1.myworkdayjobs.com|finra|FINRA", "urban.wd115.myworkdayjobs.com|urban|Urban-Careers"]
BODY = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "data"}
out = []
def txt(r):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:240]
for tok in TOKENS:
    host, tenant, site = tok.split("|")
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    h = workday_headers(host, tenant, site)
    row = {"token": tok}
    s = requests.Session(); s.headers.update(h)
    r = s.post(api, json=BODY, timeout=25); row["post"] = (r.status_code, txt(r)); time.sleep(2)
    s = requests.Session(); s.headers.update({**h, "Accept": "text/html"})
    r = s.get(f"https://{host}/{site}", timeout=25); row["page"] = (r.status_code, txt(r)[:120], list(s.cookies.keys()))
    s.headers["Accept"] = "application/json"; time.sleep(2)
    r = s.post(api, json=BODY, timeout=25); row["post_after_page"] = (r.status_code, txt(r)[:120])
    r = s.post(api, json={**BODY, "searchText": ""}, timeout=25); row["post_blank"] = (r.status_code, txt(r)[:120])
    s2 = requests.Session(); s2.headers.update({"User-Agent": WORKDAY_UA, "Accept": "application/json", "Content-Type": "application/json"})
    r = s2.post(api, json=BODY, timeout=25); row["post_min"] = (r.status_code, txt(r)[:120])
    out.append(row); print(json.dumps(row)[:600]); time.sleep(3)
Path("probe").mkdir(exist_ok=True); Path("probe/diag.json").write_text(json.dumps(out, indent=2))
