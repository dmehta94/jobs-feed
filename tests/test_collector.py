"""Offline tests: every network call is answered by a fake session."""
import json
import re
from datetime import date

import pytest

from collector import collect, government, http, sources
from collector import text as text_mod
from collector.text import (advanced_degree_required, extract_salary, location_is_ok, phd_required,
                            title_is_relevant, years_required)


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
            params = kwargs["params"]
            pairs = params.items() if isinstance(params, dict) else params
            url = url + "?" + "&".join(f"{k}={v}" for k, v in pairs)
        key = f"{method} {url}"
        self.calls.append(key)
        for pattern, body in self.routes.items():
            if re.search(pattern, key):
                if body is None:
                    return FakeResponse(404, "not found")
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
    ("Data Scientist, M-Powering Teacher Coaching", True),
    ("Teacher Effectiveness Analyst", True),
    ("Tutor, Algebra", False),
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
    ("Flexible / Remote, New Delhi, India", None, False),
    ("Remote - Europe", True, False),
    ("London, UK; New York, NY", None, True),
    ("Remote (US or Canada)", True, True),
    ("Hoboken, NJ, United States", None, True),
    ("Nashville, TN, United States", None, False),
    ("United States", None, True),
    ("US-Remote", None, True),
    ("Cambridge, MA", None, False),
    ("Remote - Cambridge, Massachusetts", None, True),
    ("Ann Arbor, MI; Cambridge, MA; Princeton, NJ; Washington, DC;", None, False),
    ("2 Locations", None, False),
    ("Multiple Locations", None, True),
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


@pytest.mark.parametrize("text,expected", [
    ("Master's degree required in public policy or a related field.", "Master's"),
    ("Requires an MPP, MPA, or similar graduate degree.", "Master's"),
    ("Minimum qualifications: PhD in economics.", "PhD"),
    ("A doctoral degree is required.", "PhD"),
    ("Bachelor's degree required; master's degree preferred.", None),
    ("Master's degree or equivalent experience required.", None),
    ("Requires a bachelor's degree and 2 years of experience, or a master's degree.", None),
    ("1. A master's degree from an accredited college; or 2. A baccalaureate degree and two years of experience.", None),
    ("Bachelor's degree required. Experience with R a plus.", None),
    ("A master's degree is a plus.", None),
    ("We value lifelong learners.", None),
    ("Minimum Qual Requirements: 1. A master's degree from an accredited college; or 2. A baccalaureate degree and two years of experience.", None),
    ("Minimum Qualifications: 1. A master's degree in statistics. 2. Two years of experience.", "Master's"),
    ("Qualifications\nMaster's degree in education research required\nStrong SQL", "Master's"),
])
def test_advanced_degree_required(text, expected):
    assert advanced_degree_required(text) == expected


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
    ('<a href="https://recruiting.ultipro.com/ABC1000/JobBoard/x">', ("unsupported:ukg", None)),
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
    fake({r"GET https://mdrc\.example/careers": '<a href="https://recruiting.ultipro.com/MDR1000/JobBoard/x">Jobs</a>'})
    org = {"name": "MDRC", "category": "Research & Policy", "tier": "1", "careers_url": "https://mdrc.example/careers"}
    health, jobs = collect.collect_org(org, {}, "2026-10-12", {})
    assert health["platform"] == "unsupported:ukg"
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
    assert len(orgs) >= 80
    assert any(o["name"] == "Anthropic" for o in orgs)
    names = [o["name"] for o in orgs]
    assert len(names) == len(set(names))
    for o in orgs:
        assert o["category"] in {"Education", "Research & Policy", "Civic Tech", "Higher Ed", "NYC Government", "AI & Tech"}
        assert o["tier"] in {"1", "2"}
        assert o["careers_url"].startswith("https://")


# ---------- discovery ----------

from collector import discovery


def test_discovery_mission_filter():
    keep = discovery.keep
    base = {"location": "New York, NY", "remote": None, "department": None}
    assert keep({**base, "org": "Newsela", "title": "Data Analyst", "description": "Help students read."})
    assert keep({**base, "org": "Ford Foundation", "title": "Research Analyst", "description": ""})
    assert not keep({**base, "org": "Citadel", "title": "Data Analyst",
                     "description": "Machine learning for markets. Equity compensation. College degree required."})
    assert not keep({**base, "org": "Newsela", "title": "Account Executive", "description": "Sell to schools."})
    assert not keep({**base, "org": "Newsela", "title": "Data Analyst", "location": "Austin, TX",
                     "description": "Help students read."})


def test_themuse_and_remotive_adapters(fake):
    fake({
        r"themuse\.com/api/public/jobs": {"page_count": 1, "results": [{
            "name": "Research Analyst", "company": {"name": "Teaching Matters"},
            "locations": [{"name": "New York, NY"}], "refs": {"landing_page": "https://muse/1"},
            "publication_date": "2026-10-10T00:00:00Z", "contents": "<p>Support teachers. $80,000 - $90,000</p>"}]},
        r"remotive\.com/api/remote-jobs": {"jobs": [
            {"title": "Data Analyst", "company_name": "Edu Co", "url": "https://rem/1",
             "candidate_required_location": "USA Only", "publication_date": "2026-10-11", "description": "students",
             "salary": "$85k - $95k", "job_type": "full_time"},
            {"title": "Data Analyst", "company_name": "EU Co", "url": "https://rem/2",
             "candidate_required_location": "Germany", "description": "students"}]},
    })
    [muse] = discovery.fetch_themuse()
    assert muse["org"] == "Teaching Matters" and muse["salary_max"] == 90000
    rem = discovery.fetch_remotive()
    assert [j["url"] for j in rem] == ["https://rem/1", "https://rem/1"]  # same feed answers both categories here
    assert rem[0]["salary_min"] == 85000


def test_adzuna_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    assert discovery.fetch_adzuna() is None


def test_collect_discovery_skips_known_and_stale(fake, monkeypatch):
    monkeypatch.setattr(discovery, "SOURCES", [("Fake", lambda: [
        discovery._job("Zearn", "Data Analyst", "New York, NY", "https://agg/1", posted="2026-10-10",
                       description="students"),
        discovery._job("Edu Org", "Research Analyst", "Remote", "https://agg/2", remote=True,
                       posted="2026-10-10", description="students"),
        discovery._job("Edu Org", "Data Analyst", "Remote", "https://agg/3", remote=True,
                       posted="2026-01-01", description="students"),
    ]), ("Keyless", lambda: None)])
    known = [{"org": "Zearn", "title": "Data Analyst"}]
    results = collect.collect_discovery("2026-10-12", {}, known)
    (h1, jobs), (h2, _) = results
    assert [j["url"] for j in jobs] == ["https://agg/2"]
    assert jobs[0]["category"] == "Discovered" and jobs[0]["tier"] == 3
    assert h2["status"].startswith("skipped")


# ---------- platforms added from probe results (fixtures are real captured pages) ----------

import json as _json
from pathlib import Path as _Path
from collector import sources_extra

FIX = _Path(__file__).parent / "fixtures"


def _links(org):
    return " ".join(_json.loads((FIX / "ats_links.json").read_text())[org])


@pytest.mark.parametrize("org,platform,token_start", [
    ("Child Trends", "adp", "70c1bbee-a65c-4b42-bb04-9dcb8"),
    ("Abt Global", "oracle", "egpy.fa.us2.oraclecloud.com|JoinAbt"),
    ("Pearson", "oracle", "hccz.fa.em3.oraclecloud.com|CX_2"),
    ("Arnold Ventures", "paylocity", "17519654-d224-4f10-9a5f-2b7ad3cd5a77"),
    ("McGraw Hill", "icims", "careers-mheducation.icims.com"),
    ("Imagine Learning", "jobvite", "imagine-learning"),
    ("CommonLit", "pinpoint", "commonlit"),
    ("Kiddom", "ashby", "kiddom"),
    ("Project Evident", "jazzhr", "projectevident"),
])
def test_detect_real_platform_links(org, platform, token_start):
    found, token = sources.detect_in_html(_links(org))
    assert found == platform and token.startswith(token_start)


def test_links_reader_finds_paycom_and_paylocity_postings():
    assert sources_extra.has_job_links(_links("Common Sense Media"))
    assert sources_extra.has_job_links(_links("Education Analytics"))
    jobs = sources_extra.fetch_links("https://x", raw=_links("Common Sense Media"))
    assert len(jobs) == 5 and all("paycomonline.net" in j["url"] for j in jobs)


def test_jazzhr_reader_on_real_page(fake):
    fake({r"innovatingjustice\.applytojob\.com/apply$": (FIX / "jazzhr_list.html").read_text()})
    jobs = sources_extra.fetch_jazzhr("innovatingjustice")
    assert len(jobs) >= 10
    assert any(j["title"] == "Director, Clinical Practice and Training" for j in jobs)
    assert all(j["url"].startswith("https://innovatingjustice.applytojob.com/apply/") for j in jobs)


def test_pinpoint_reader_on_real_feed(fake):
    fake({r"commonlit\.pinpointhq\.com/jobs\.rss": (FIX / "pinpoint.rss").read_text()})
    jobs = sources_extra.fetch_pinpoint("commonlit")
    assert jobs and jobs[0]["title"] == "Sales Manager" and jobs[0]["description"]


def test_adp_reader(fake):
    fake({r"job-requisitions\?": {"jobRequisitions": [
        {"itemID": "9201", "requisitionTitle": "Research Analyst", "postDate": "2026-10-01T00:00:00Z",
         "requisitionLocations": [{"address": {"cityName": "New York", "countrySubdivisionLevel1": {"codeValue": "NY"}}}]}],
        "meta": {"totalNumber": 1}},
          r"job-requisitions/9201": {"requisitionDescription": "<p>Bachelor's required. $80,000 - $90,000</p>"}})
    [job] = sources_extra.fetch_adp("abc")
    assert job["location"] == "New York, NY" and job["posted"] == "2026-10-01"
    assert "jobId=9201" in job["url"]
    assert "80,000" in sources.fetch_detail(job["_detail"])


def test_oracle_reader(fake):
    fake({r"recruitingCEJobRequisitions\?": {"items": [{"requisitionList": [
        {"Id": "123", "Title": "Data Analyst", "PrimaryLocation": "New York, NY", "PostedDate": "2026-10-02"}]}]}})
    jobs = sources_extra.fetch_oracle("egpy.fa.us2.oraclecloud.com|CX_3001")
    assert len(jobs) == 1 and jobs[0]["url"].endswith("/sites/CX_3001/job/123")


def test_greenhouse_falls_back_to_board_html(fake):
    board = ('<a href="https://job-boards.greenhouse.io/deepmind/jobs/7606920?gh_jid=7606920">'
             '<p class="body">Research Scientist, Education</p><p class="body">London, UK</p></a>')
    fake({r"boards-api\.greenhouse\.io/v1/boards/deepmind/jobs": None,
          r"GET https://job-boards\.greenhouse\.io/deepmind$": board})
    jobs = sources.fetch_greenhouse("deepmind")
    assert [j["title"] for j in jobs] == ["Research Scientist, Education"]


# ---------- remote roles based elsewhere ----------

@pytest.mark.parametrize("text,expected", [
    ("This position can be performed fully remotely at a non-Harvard location.", True),
    ("Location: Remote (US)", True),
    ("We offer telework flexibility.", True),
    ("Fully remote; must reside in an approved state.", True),
    ("Hybrid schedule with telework up to two days per week.", False),
    ("This role is hybrid, with three days a week in our Boston office.", False),
    ("This position is not eligible for remote work.", False),
    ("Remote option; must reside within commuting distance of Seattle.", False),
    ("Join our team in Chicago.", False),
    ("", False),
])
def test_remote_from_text(text, expected):
    assert text_mod.remote_from_text(text) is expected


def test_collect_org_keeps_out_of_area_roles_that_are_remote(fake):
    fake({r"boards/cepr/jobs": {"jobs": [
        {"title": "Data Scientist, Teacher Coaching", "absolute_url": "u1", "location": {"name": "Cambridge, MA"},
         "content": "This position can be performed fully remotely."},
        {"title": "Data Scientist, Lab", "absolute_url": "u2", "location": {"name": "Cambridge, MA"},
         "content": "This role is on-site only."},
    ]}})
    org = {"name": "CEPR", "category": "Higher Ed", "tier": "2", "careers_url": "", "ats": "greenhouse", "token": "cepr"}
    health, jobs = collect.collect_org(org, {}, "2026-10-12", {})
    assert [j["url"] for j in jobs] == ["u1"]
    assert jobs[0]["remote"] is True


def test_collect_org_flags_empty_guessed_board(fake):
    fake({r"boards/rand/jobs": {"jobs": []}})
    org = {"name": "RAND", "category": "Research & Policy", "tier": "2", "careers_url": ""}
    cache = {"RAND": {"platform": "greenhouse", "token": "rand"}}
    health, jobs = collect.collect_org(org, cache, "2026-10-12", {})
    assert health["status"].startswith("empty") and "RAND" not in cache


def test_smartrecruiters_searches_big_boards_by_keyword(fake):
    def listing(key, kwargs):
        if "q=" not in key:
            return {"totalFound": 1500, "content": []}
        if "q=data&" in key + "&" or key.endswith("q=data"):
            return {"totalFound": 1, "content": [{"id": "9", "name": "Data Scientist",
                                                  "location": {"fullLocation": "Cambridge, MA", "remote": True}}]}
        return {"totalFound": 0, "content": []}
    fake({r"api\.smartrecruiters\.com/v1/companies/HarvardUniversity/postings\?": listing})
    jobs = sources.fetch_smartrecruiters("HarvardUniversity")
    assert [(j["title"], j["remote"]) for j in jobs] == [("Data Scientist", True)]


@pytest.mark.parametrize("title,shown", [
    ("Data Scientist, M-Powering Teacher Coaching", True),
    ("Associate Demographic Scientist", True),
    ("Senior Research Assistant, The Hamilton Project", True),
    ("Research Data Manager", True),
    ("Data Specialist (Contract-to-Hire, Remote)", True),
    ("Senior Data Scientist", False),
    ("Contracts Manager", False),
    ("Financial Analyst", False),
])
def test_title_gate(title, shown):
    from collector.fit import title_gate
    assert (title_gate(title) is None) is shown


def test_edtech_reads_sitemap_then_structured_data(fake):
    page = (_Path(__file__).parent / "fixtures" / "edtech_job.html").read_text()
    sitemap = ("<urlset><url><loc>https://www.edtech.com/jobs/data-scientist-m-powering-teacher-coaching-31890</loc>"
               "<lastmod>2026-10-14T07:51:32+00:00</lastmod></url>"
               "<url><loc>https://www.edtech.com/jobs/account-executive-79735</loc>"
               "<lastmod>2026-10-14T07:50:00+00:00</lastmod></url></urlset>")
    session = fake({r"GET https://www\.edtech\.com/sitemap\.xml": sitemap,
                    r"GET https://www\.edtech\.com/jobs/data-scientist": page})
    [job] = discovery.fetch_edtech()
    assert job["org"] == "Harvard Graduate School of Education"
    assert job["title"] == "Data Scientist, M-Powering Teacher Coaching"
    assert job["remote"] is True and job["location"].startswith("Remote")
    assert (job["salary_min"], job["salary_max"]) == (90000, 95000)
    assert job["posted"] == "2026-10-14" and "Center for Education Policy Research" in job["description"]
    assert discovery.keep(job)
    assert not any("account-executive" in c for c in session.calls)


def test_higheredjobs_reads_remote_search_results(fake):
    page = (_Path(__file__).parent / "fixtures" / "higheredjobs_remote.html").read_text()
    fake({r"advanced_action\.cfm": page, r"details\.cfm\?JobCode=": '<div id="jobDesc"><p>Fully remote.</p></div><footer>'})
    jobs = discovery.fetch_higheredjobs()
    titles = {j["title"]: j for j in jobs}
    job = titles["Research Informatics Analyst School of Medicine Brain Health"]
    assert job["org"] == "Emory University" and job["remote"] is True and job["location"] == "Online/Remote"
    sbs = titles["Senior Business Systems Analyst - Workday Adaptive Planning"]
    assert (sbs["salary_min"], sbs["salary_max"]) == (80128, 128226)


def test_pageup_listing(fake):
    page = (_Path(__file__).parent / "fixtures" / "pageup_listing.html").read_text()
    fake({r"careers\.norc\.org/en-us/listing/": page})
    [job] = sources_extra.fetch_pageup("https://careers.norc.org/en-us")
    assert job["title"].startswith("Data Scientist I - Computational Research")
    assert job["location"] == "Chicago - 300 E Randolph St"
    assert job["url"] == ("https://careers.norc.org/en-us/job/503995/"
                          "data-scientist-i-computational-research-statistics-machine-learning")


def test_eightfold_search_and_remote_local(fake):
    def search(key, kwargs):
        if "query=data&" not in key or "start=0" not in key:
            return {"data": {"positions": [], "count": 0}}
        return {"data": {"count": 2, "positions": [
            {"id": 1, "name": "Research Data Analyst", "locations": ["Remote, United States of America"],
             "workLocationOption": "remote", "postedTs": 1792454400, "positionUrl": "/careers/job/1"},
            {"id": 2, "name": "Data Specialist", "locations": ["Remote, Kansas, United States of America"],
             "workLocationOption": "remote_local", "positionUrl": "/careers/job/2"}]}}
    fake({r"careers\.ca\.example/api/pcsx/search": search,
          r"careers\.ca\.example/api/pcsx/position_details": {"data": {"jobDescription": "<p>Bachelor's required.</p>"}}})
    jobs = sources_extra.fetch_eightfold("careers.ca.example|ca.example")
    one, two = sorted(jobs, key=lambda j: j["title"])[::-1]
    assert one["remote"] is True and one["url"] == "https://careers.ca.example/careers/job/1"
    assert two["remote"] is None and not location_is_ok(two["location"], two["remote"])
    assert "Bachelor" in sources.fetch_detail(one["_detail"])


def test_trakstar_feed(fake):
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><item><title>Research Associate</title>'
           '<link>https://edn.hire.trakstar.com/jobs/abc</link><description>&lt;p&gt;Remote role&lt;/p&gt;</description>'
           '<pubDate>Mon, 19 Oct 2026 12:00:00 +0000</pubDate></item></channel></rss>')
    fake({r"edn\.hire\.trakstar\.com/jobfeeds/edn": rss})
    [job] = sources_extra.fetch_trakstar("edn")
    assert job["title"] == "Research Associate" and job["posted"] == "2026-10-19"
    assert job["url"] == "https://edn.hire.trakstar.com/jobs/abc"
