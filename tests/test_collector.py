"""Offline tests: every network call is answered by a fake session."""
import json
import re
from datetime import date

import pytest

from collector import collect, government, http, sources
from collector.text import extract_salary, location_is_ok, phd_required, title_is_relevant, years_required


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class FakeSession:
    """Routes requests by regex on 'METHOD url'. Unmatched requests get a 404."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kwargs):
        if kwargs.get("params"):
            url = url + "?" + "&".join(f"{k}={v}" for k, v in kwargs["params"].items())
        key = f"{method} {url}"
        self.calls.append(key)
        for pattern, body in self.routes.items():
            if re.search(pattern, key):
                return FakeResponse(200, body(key, kwargs) if callable(body) else body)
        return FakeResponse(404, "not found")


@pytest.fixture
def fake(monkeypatch):
    def install(routes):
        session = FakeSession(routes)
        monkeypatch.setattr(http, "session", session)
        return session
    return install


# ---------- filters ----------

@pytest.mark.parametrize("title,expected", [
    ("Data Analyst", True),
    ("Research Associate, Efficacy", True),
    ("Learning Scientist", True),
    ("Senior Research Analyst", True),
    ("Director of Research", False),
    ("Account Executive, Data Products", False),
    ("Math Teacher", False),
    ("Software Engineer, Data Platform", False),
    ("Office Manager", False),
])
def test_title_filter(title, expected):
    assert title_is_relevant(title) is expected


@pytest.mark.parametrize("loc,remote,expected", [
    ("New York, NY", None, True),
    ("Remote - US", None, True),
    ("Washington, DC", None, False),
    ("Washington, DC or New York, NY", None, True),
    ("Chicago, IL", True, True),
    ("", None, True),
    ("San Francisco", None, False),
])
def test_location_filter(loc, remote, expected):
    assert location_is_ok(loc, remote) is expected


def test_salary_extraction():
    assert extract_salary("The salary range is $85,000 - $95,000 per year.")[:2] == (85000, 95000)
    assert extract_salary("Pay: $70k–$90k")[:2] == (70000, 90000)
    assert extract_salary("Starting at $92,500 annually")[:2] == (92500, 92500)
    assert extract_salary("$25 - $30 per hour")[:2] == (None, None)
    assert extract_salary("no pay info")[:2] == (None, None)


def test_years_and_phd():
    assert years_required("Requires 2+ years of experience with SQL and 5 years of something else") == 2
    assert years_required("3-5 years of relevant experience") == 3
    assert years_required("A strong portfolio") is None
    assert phd_required("PhD required in economics or related field")
    assert not phd_required("PhD preferred but not required")


# ---------- detection ----------

@pytest.mark.parametrize("snippet,expected", [
    ('<a href="https://job-boards.greenhouse.io/zearn">Jobs</a>', ("greenhouse", "zearn")),
    ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=newvisions"></script>', ("greenhouse", "newvisions")),
    ('<a href="https://jobs.lever.co/recidiviz/abc">x</a>', ("lever", "recidiviz")),
    ('<iframe src="https://jobs.ashbyhq.com/khanacademy?embed=js">', ("ashby", "khanacademy")),
    ('<a href="https://careers.smartrecruiters.com/PearsonEducation">', ("smartrecruiters", "PearsonEducation")),
    ('<a href="https://apply.workable.com/quill/">', ("workable", "quill")),
    ('<a href="https://collegeboard.wd1.myworkdayjobs.com/en-US/Careers/job/x">',
     ("workday", "collegeboard.wd1.myworkdayjobs.com|collegeboard|Careers")),
    ('<a href="https://tntp.bamboohr.com/careers">', ("bamboohr", "tntp")),
    ('<a href="https://recruiting.paylocity.com/recruiting/jobs/All/abc">', ("unsupported:paylocity", None)),
    ("<p>No jobs right now</p>", (None, None)),
])
def test_detect_in_html(snippet, expected):
    assert sources.detect_in_html(snippet) == expected


def test_detect_follows_linked_jobs_page(fake):
    fake({
        r"GET https://example\.org/careers$": '<a href="/careers/open-positions">See open positions</a>',
        r"GET https://example\.org/careers/open-positions": '<iframe src="https://job-boards.greenhouse.io/exampleorg">',
    })
    platform, token, how = sources.detect("https://example.org/careers")
    assert (platform, token) == ("greenhouse", "exampleorg")
    assert "linked page" in how


def test_probe_requires_matching_board_name(fake):
    fake({
        r"boards/newclassrooms$": {"name": "New Classrooms Innovation Partners"},
        r"boards/amplify$": {"name": "Amplify Snacks"},
    })
    assert sources.probe("New Classrooms", "https://newclassrooms.org/careers/") == ("greenhouse", "newclassrooms")
    assert sources.probe("Amplify", "https://amplify.com/careers/") == (None, None)


@pytest.mark.parametrize("org,board,expected", [
    ("Zearn", "Zearn", True),
    ("Code for America", "Code for America Labs, Inc.", False),
    ("Amplify", "Amplify Education, Inc.", True),
    ("Amplify", "Amplify Snacks", False),
    ("DataKind", "DataKind", True),
    ("Research Alliance for NYC Schools (NYU)", "Research Alliance for NYC Schools", True),
])
def test_names_match(org, board, expected):
    assert sources.names_match(org, board) is expected


# ---------- adapters ----------

def test_greenhouse_adapter(fake):
    fake({r"boards/zearn/jobs\?content=true": {"jobs": [{
        "title": "Data Analyst", "absolute_url": "https://job-boards.greenhouse.io/zearn/jobs/1",
        "location": {"name": "New York, NY"}, "updated_at": "2026-10-01T12:00:00-04:00",
        "first_published": "2026-09-30T09:00:00-04:00",
        "departments": [{"name": "Research"}],
        "content": "&lt;p&gt;Salary: $85,000 - $95,000. 2+ years of experience.&lt;/p&gt;",
    }]}})
    [job] = sources.fetch_greenhouse("zearn")
    assert job["title"] == "Data Analyst"
    assert job["posted"] == "2026-09-30"
    assert (job["salary_min"], job["salary_max"]) == (85000, 95000)
    assert "<p>" not in job["description"]


def test_lever_adapter(fake):
    fake({r"api\.lever\.co/v0/postings/recidiviz": [{
        "text": "Data Analyst", "hostedUrl": "https://jobs.lever.co/recidiviz/1", "createdAt": 1759320000000,
        "categories": {"location": "Remote", "team": "Data", "commitment": "Full-time"},
        "workplaceType": "remote", "descriptionPlain": "About the role.",
        "lists": [{"text": "Requirements", "content": "<li>1+ years of experience</li>"}],
        "salaryRange": {"min": 85000, "max": 95000, "currency": "USD", "interval": "per-year-salary"},
    }]})
    [job] = sources.fetch_lever("recidiviz")
    assert job["remote"] is True
    assert (job["salary_min"], job["salary_max"]) == (85000, 95000)
    assert "1+ years" in job["description"]


def test_ashby_adapter(fake):
    fake({r"api\.ashbyhq\.com/posting-api/job-board/acme": {"jobs": [
        {"title": "Research Analyst", "location": "New York", "jobUrl": "https://jobs.ashbyhq.com/acme/1",
         "isRemote": False, "publishedAt": "2026-10-02T00:00:00Z", "descriptionPlain": "Analyze data.",
         "compensation": {"compensationTierSummary": "$80K – $100K",
                          "summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR",
                                                 "minValue": 80000, "maxValue": 100000}]}},
        {"title": "Hidden", "isListed": False},
    ]}})
    jobs = sources.fetch_ashby("acme")
    assert len(jobs) == 1
    assert (jobs[0]["salary_min"], jobs[0]["salary_max"]) == (80000, 100000)


def test_workday_adapter_and_detail(fake):
    def listing(key, kwargs):
        if kwargs["json"]["searchText"] == "research" and kwargs["json"]["offset"] == 0:
            return {"jobPostings": [{"title": "Research Scientist", "externalPath": "/job/NY/Research-Scientist_R1",
                                     "locationsText": "New York, NY", "postedOn": "Posted Today"}]}
        return {"jobPostings": []}
    fake({
        r"POST https://acme\.wd5\.myworkdayjobs\.com/wday/cxs/acme/Careers/jobs$": listing,
        r"GET https://acme\.wd5\.myworkdayjobs\.com/wday/cxs/acme/Careers/job/NY/Research-Scientist_R1":
            {"jobPostingInfo": {"jobDescription": "<p>Pay range $90,000 - $110,000</p>"}},
    })
    [job] = sources.fetch_workday("acme.wd5.myworkdayjobs.com|acme|Careers")
    assert job["posted"] == date.today().isoformat()
    assert job["url"] == "https://acme.wd5.myworkdayjobs.com/Careers/job/NY/Research-Scientist_R1"
    assert "90,000" in sources.fetch_detail(job["_detail"])


# ---------- end to end ----------

def test_collect_org_filters_caches_and_tracks_first_seen(fake):
    fake({
        r"GET https://zearn\.example/careers": '<a href="https://job-boards.greenhouse.io/zearn">Jobs</a>',
        r"boards/zearn/jobs": {"jobs": [
            {"title": "Data Analyst", "absolute_url": "u1", "location": {"name": "New York, NY"}, "content": ""},
            {"title": "Account Executive", "absolute_url": "u2", "location": {"name": "New York, NY"}, "content": ""},
            {"title": "Research Analyst", "absolute_url": "u3", "location": {"name": "Austin, TX"}, "content": ""},
        ]},
    })
    org = {"name": "Zearn", "category": "Education", "tier": "1", "careers_url": "https://zearn.example/careers"}
    cache = {}
    seen = {"u1": {"first_seen": "2026-09-01", "last_seen": "2026-10-05"}}
    health, jobs = collect.collect_org(org, cache, "2026-10-12", seen)
    assert health["status"] == "ok" and health["total"] == 3 and health["relevant"] == 1
    assert cache["Zearn"] == {"platform": "greenhouse", "token": "zearn"}
    assert jobs[0]["first_seen"] == "2026-09-01" and jobs[0]["new_this_week"] is False
    assert seen["u1"]["last_seen"] == "2026-10-12"


def test_collect_org_reports_unsupported_platform(fake):
    fake({r"GET https://mdrc\.example/careers": '<a href="https://recruiting.paylocity.com/x">Jobs</a>'})
    org = {"name": "MDRC", "category": "Research & Policy", "tier": "1", "careers_url": "https://mdrc.example/careers"}
    health, jobs = collect.collect_org(org, {}, "2026-10-12", {})
    assert health["platform"] == "unsupported:paylocity"
    assert jobs == []


def test_nyc_source_filters_and_dedupes(fake):
    rows = [
        {"job_id": "1", "agency": "DEPT OF EDUCATION", "business_title": "Data Analyst", "civil_service_title": "STAFF ANALYST",
         "salary_range_from": "70000", "salary_range_to": "90000", "salary_frequency": "Annual",
         "posting_date": "2026-10-01T00:00:00.000", "title_classification": "Competitive-1"},
        {"job_id": "1", "agency": "DEPT OF EDUCATION", "business_title": "Data Analyst", "civil_service_title": "STAFF ANALYST"},
        {"job_id": "2", "agency": "DOT", "business_title": "Bridge Painter", "civil_service_title": "PAINTER"},
        {"job_id": "3", "agency": "NYPD", "business_title": "Research Scientist", "civil_service_title": "CITY RESEARCH SCIENTIST",
         "salary_range_from": "40", "salary_range_to": "50", "salary_frequency": "Hourly"},
    ]
    fake({r"data\.cityofnewyork\.us/resource/kpav-sd4t\.json": rows})
    total, jobs = government.fetch_nyc()
    assert total == 4
    assert [j["extra"]["nyc_job_id"] for j in jobs] == ["1", "3"]
    assert jobs[0]["salary_max"] == 90000 and jobs[0]["extra"]["exam_required"] is True
    assert jobs[1]["salary_max"] is None  # hourly pay isn't treated as annual


def test_usajobs_skipped_without_key(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    assert government.fetch_usajobs() == (None, [])


def test_orgs_csv_is_well_formed():
    orgs = collect.load_orgs()
    assert len(orgs) >= 60
    names = [o["name"] for o in orgs]
    assert len(names) == len(set(names))
    for o in orgs:
        assert o["category"] in {"Education", "Research & Policy", "Civic Tech", "Higher Ed", "NYC Government"}
        assert o["tier"] in {"1", "2"}
        assert o["careers_url"].startswith("https://")
