"""New York boards that aren't an employer's own hiring platform: StateJobsNY, cuny.jobs, the jobboard.io boards
(NYN Jobs, City & State New York Jobs), and the NYS Attorney General's postings.

Each reader returns postings in the same shape as the platform readers, with the employer in "org" so
collect.finish keeps it instead of the board's name.
"""
import html as htmllib
import re
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from . import http
from .sources_extra import _job, page_text
from .text import html_to_text, iso_date

# ---------- StateJobsNY ----------

STATEJOBS = "https://statejobs.ny.gov/public"
# NYC's five counties and Nassau, by the names StateJobsNY uses, mapped to place names the location filter knows.
STATE_COUNTIES = {"new york": "Manhattan, NY", "kings": "Brooklyn, NY", "queens": "Queens, NY", "bronx": "Bronx, NY",
                  "richmond": "Staten Island, NY", "nassau": "Nassau County, NY"}
_ROW = re.compile(r'<tr class="(?:even|odd)">(.*?)</tr>', re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LINK_ID = re.compile(r"vacancyDetailsView\.cfm\?id=(\d+)")
_SHORT_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})$")
_STATE_SALARY = re.compile(r"Salary Range From \$?([\d,]+(?:\.\d+)?) to \$?([\d,]+(?:\.\d+)?)", re.I)


def _short_date(text):
    m = _SHORT_DATE.search((text or "").strip())
    return f"20{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def parse_statejobs_table(raw):
    jobs = []
    for row in _ROW.findall(raw or ""):
        cells = [htmllib.unescape(html_to_text(c)) for c in _CELL.findall(row)]
        link = _LINK_ID.search(row)
        if len(cells) < 7 or not link:
            continue
        vid, title, _grade, posted, deadline, agency, county = cells[:7]
        location = STATE_COUNTIES.get(county.strip().lower(), f"{county.strip()} County, NY (New York State)")
        job = _job(title, location, f"{STATEJOBS}/vacancyDetailsView.cfm?id={link.group(1)}",
                   posted=_short_date(posted), department=agency, detail=("statejobsny", link.group(1)))
        job["org"] = f"NYS {agency}" if agency else "New York State"
        job["extra"] = {"post_until": _short_date(deadline), "state_vacancy_id": link.group(1)}
        jobs.append(job)
    return jobs


def fetch_statejobsny(_token=None):
    return parse_statejobs_table(http.get_text(f"{STATEJOBS}/vacancyTable.cfm"))


def statejobs_detail_text(raw):
    """The vacancy's print page as text, from "Date Posted" on, with its salary range written the way
    extract_salary reads ranges ("$52,694 - $64,321")."""
    text = page_text(raw)
    start = text.find("Date Posted")
    text = text[start:] if start >= 0 else text
    def fmt(m):
        lo, hi = (float(x.replace(",", "")) for x in m.groups())
        return f"Salary Range ${lo:,.0f} - ${hi:,.0f}"
    return _STATE_SALARY.sub(fmt, text)


def statejobs_detail(vacancy_id):
    return statejobs_detail_text(http.get_text(f"{STATEJOBS}/vacancyDetailsPrint.cfm?id={vacancy_id}"))


# ---------- RSS boards ----------

def _rss_items(raw):
    root = ElementTree.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw)
    for item in root.iter("item"):
        posted = None
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            pass
        yield ((item.findtext("title") or "").strip(), (item.findtext("link") or "").strip(),
               item.findtext("description") or "", posted)


_CUNY_TITLE = re.compile(r"^\((?:USA-)?(?:NY-)?([^)]*)\)\s*(.*)$")


def parse_cuny_rss(raw):
    """cuny.jobs titles carry the place: "(USA-NY-Queens) Data Analyst, Institutional Research"."""
    jobs = []
    for title, link, desc, posted in _rss_items(raw):
        m = _CUNY_TITLE.match(title)
        place, title = (m.group(1), m.group(2)) if m else ("", title)
        place = place.replace("New York", "New York City") if place.strip().lower() == "new york" else place
        job = _job(title, f"{place}, NY" if place else "", link, posted=posted, description=html_to_text(desc))
        job["org"] = "CUNY"
        jobs.append(job)
    return jobs


def fetch_cuny(url="https://cuny.jobs/jobs/feed/rss/"):
    return parse_cuny_rss(http.get_text(url))


_BOARD_TITLE = re.compile(r"^(.*?)\s+at\s+(.+?)(?:\s+\(([^()]*)\))?\s*$")


def parse_jobboard_rss(raw):
    """jobboard.io boards title items "Data Analyst at Some Org (New York, NY, USA)"; descriptions are cut short,
    so the posting page is read for the full text."""
    jobs = []
    for title, link, desc, posted in _rss_items(raw):
        m = _BOARD_TITLE.match(htmllib.unescape(title))
        role, employer, place = (m.group(1), m.group(2), m.group(3) or "") if m else (title, "", "")
        job = _job(role, place, link, posted=posted, description="", detail=("page", link))
        # The teaser often opens with the pay range ("$72,000 - $77,000The YMCA ..."); keep it if the page lacks one.
        teaser = _job("", "", "", description=html_to_text(desc))
        job.update({k: teaser[k] for k in ("salary_min", "salary_max", "salary_text") if teaser[k] is not None})
        if employer:
            job["org"] = employer
        jobs.append(job)
    return jobs


def fetch_jobboard(host):
    return parse_jobboard_rss(http.get_text(f"https://{host}/jobs.rss"))


# ---------- NYS Office of the Attorney General ----------

AG = "https://ag.ny.gov"
AG_PAGES = ("/job-postings/other", "/job-postings/it")  # analyst, research, and data roles are listed under these


def _ag_field(row, name):
    m = re.search(rf'field-job-post-{name}">.*?<span class="field-content">(.*?)</span>', row, re.S)
    return html_to_text(m.group(1)) if m else ""


def parse_ag_page(raw):
    jobs = []
    for row in re.split(r'<div class="views-row">', raw or "")[1:]:
        title = re.search(r'views-field-title">.*?<a href="([^"]+)">(.*?)</a>', row, re.S)
        if not title:
            continue
        bureau, location, ref = _ag_field(row, "bureau"), _ag_field(row, "location"), _ag_field(row, "reference-number")
        url = title.group(1) if title.group(1).startswith("http") else AG + title.group(1)
        job = _job(html_to_text(title.group(2)), location, url, department=bureau,
                   description=f"Bureau: {bureau}. Location: {location}. Reference number: {ref}. "
                               "The full posting, with duties, qualifications, and pay, is in the linked PDF.")
        job["org"] = "NYS Office of the Attorney General"
        # The visible date, not the time element's datetime, which is the end of that day in UTC (the next day).
        job["extra"] = {"post_until": iso_date(_ag_field(row, "deadline")), "ag_reference": ref}
        jobs.append(job)
    return jobs


def fetch_ag(_token=None):
    jobs, seen = [], set()
    for path in AG_PAGES:
        for job in parse_ag_page(http.get_text(AG + path)):
            if job["url"] not in seen:
                seen.add(job["url"])
                jobs.append(job)
    return jobs


ADAPTERS = {
    "statejobsny": fetch_statejobsny,
    "cuny_rss": fetch_cuny,
    "jobboard_rss": fetch_jobboard,
    "nys_ag": fetch_ag,
}
