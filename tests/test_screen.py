from datetime import datetime, timezone

from collector import collect
from collector.screen import drop_reason, flags, screen, trim

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


def job(**kw):
    base = {"org": "MDRC", "title": "Research Analyst", "location": "New York, NY", "remote": False,
            "url": "https://example.org/1", "first_seen": "2026-11-26", "first_seen_at": "2026-11-26T22:30:00+00:00",
            "salary_max": 85000, "years_required": 2, "advanced_degree_required": None, "phd_required": False,
            "description": "Responsibilities: Analyze data. Qualifications: Bachelor's degree. Hybrid, two days a week."}
    base.update(kw)
    return base


def reason(**kw):
    r = drop_reason(job(**kw))
    return r and r[0]


def test_a_plain_fit_passes():
    assert drop_reason(job()) is None


def test_pay_floor_drops_only_known_annual_pay_under_60k():
    assert reason(salary_max=58491) == "pay below the $60K floor"
    assert reason(salary_max=None) is None
    assert reason(salary_max=28.5) is None  # hourly: left to the reader, flagged


def test_pay_between_floors_is_flagged_bridge_only():
    assert reason(salary_max=70000) is None
    assert "pay tops out below $80K: bridge track only" in flags(job(salary_max=70000))
    assert "pay not listed" in flags(job(salary_max=None))
    assert any("hourly" in f for f in flags(job(salary_max=28.5)))


def test_degree_and_years():
    assert reason(advanced_degree_required="Master's") == "advanced degree required"
    assert reason(phd_required=True) == "advanced degree required"
    fed = ("To qualify for the GS-11 on Education alone, you must have a Ph.D. You can also qualify based on a "
           "combination of higher level graduate education and specialized experience.")
    assert reason(phd_required=True, advanced_degree_required="PhD", description=fed) is None
    assert any("experience route" in f for f in flags(job(advanced_degree_required="PhD", description=fed)))
    assert reason(years_required=5) == "5+ years required"
    assert reason(years_required=4) is None


def test_excluded_employers():
    assert reason(org="New York City Police Department") is not None
    assert reason(org="Department of Correction") is not None
    assert reason(org="Booz Allen Hamilton") == "defense contractor"
    assert reason(org="OpenAI") == "frontier AI or big-tech AI employer"
    assert reason(org="Google") == "frontier AI or big-tech AI employer"
    assert reason(org="Google DeepMind") is None
    assert reason(org="Anthropic") is None
    assert reason(org="KIPP NYC") == "charter or private school"
    # Oversight bodies and reform nonprofits are fine.
    assert reason(org="Civilian Complaint Review Board") is None
    assert reason(org="Recidiviz") is None


def test_title_keywords():
    assert reason(title="Financial Analyst") == "finance or sales title"
    assert reason(title="Sales Development Representative") == "finance or sales title"
    assert reason(title="Correction Officer") is not None
    assert reason(title="Police Cadet") is not None
    # Naming the subject isn't the job; reform and oversight work stays in.
    assert reason(org="Vera Institute of Justice", title="Research Analyst, Policing") is None
    assert reason(org="Civilian Complaint Review Board", title="Police Accountability Analyst") is None
    assert reason(title="Military Family Services Coordinator") is None
    # Grant and program titles can be research work; the reader decides.
    assert reason(title="Grants Data Analyst") is None
    assert reason(title="Program Coordinator") is None


def test_locations():
    assert reason(location="198 E161St Street Bronx N.Y.") is not None
    assert reason(location="Bronx, NY", remote=True) is None
    assert reason(location="Bronx, NY | Manhattan, NY") is None  # one listed site passes
    on_site = "Responsibilities: Analyze. This is an on-site position."
    assert reason(location="Washington, DC", description=on_site) == "DC-area role that is fully on-site"
    assert reason(location="Washington, DC", description=on_site + " Hybrid schedule available.") is None
    assert "DC area: check in-office days" in flags(job(location="Washington, DC", description="Analyze data."))
    assert "worksite not stated" in flags(job(location=""))


def test_trim_keeps_duties_requirements_and_key_facts():
    desc = ("We are a research nonprofit doing important work. About Us: Founded in 1974 we have many offices "
            "and a long history. Responsibilities: Clean survey data. Write memos. Minimum Qualifications: "
            "Bachelor's degree and 2 years of experience. Benefits: Generous PTO. Dental plans. Employees work "
            "two days a week in the office. Equal Opportunity: We hire everyone.")
    out = trim(desc)
    assert "Clean survey data" in out and "Bachelor's degree" in out
    assert "two days a week" in out  # a key fact rescued from a dropped section
    assert "Founded in 1974" not in out and "Generous PTO" not in out and "We hire everyone" not in out


def test_trim_caps_length():
    assert len(trim("Responsibilities: " + "Analyze data carefully. " * 1000)) <= 3600


def test_window_and_output_shape():
    feed = {"generated_at": "2026-11-26T22:40:00+00:00", "jobs": [
        job(),
        job(url="https://example.org/old", first_seen="2026-11-16", first_seen_at=None),
        job(url="https://example.org/2", title="Budget Analyst"),
    ]}
    out = screen(feed, now=NOW)
    assert [c["url"] for c in out["candidates"]] == ["https://example.org/1"]
    assert out["dropped_by_reason"] == {"finance or sales title": 1}
    assert "description" not in out["candidates"][0]
    assert out["candidates"][0]["requirements_and_duties"]


def test_finish_stamps_first_seen_at_for_new_postings_only():
    seen = {"https://example.org/known": {"first_seen": "2026-11-02", "last_seen": "2026-11-23"}}
    new = collect.finish({"title": "Analyst", "url": "https://example.org/new"}, org="X", category="C", tier=1,
                         source="s", today="2026-11-26", seen=seen)
    old = collect.finish({"title": "Analyst", "url": "https://example.org/known"}, org="X", category="C", tier=1,
                         source="s", today="2026-11-26", seen=seen)
    assert new["first_seen_at"] == collect.RUN_AT
    assert old["first_seen_at"] == "2026-11-02T00:00:00+00:00"
    assert seen["https://example.org/new"]["first_seen_at"] == collect.RUN_AT


def test_qcc_math_teaching_is_its_own_lane():
    from collector.text import is_qcc_math_teaching
    qcc = "Queensborough Community College invites applications for Adjunct Lecturers."
    assert is_qcc_math_teaching({"title": "Adjunct Lecturer - Mathematics and Computer Science Department",
                                 "description": qcc})
    assert is_qcc_math_teaching({"title": "Adjunct Lecturer - Mathematics (College Now Program)",
                                 "org": "CUNY Queensborough Community College"})
    # Only QCC, only math, only adjunct/lecturer titles.
    assert not is_qcc_math_teaching({"title": "Adjunct Lecturer - Mathematics",
                                     "description": "Kingsborough Community College seeks adjuncts."})
    assert not is_qcc_math_teaching({"title": "Adjunct Lecturer - Statistics (Business Department)",
                                     "description": qcc})
    assert not is_qcc_math_teaching({"title": "Adjunct Lecturer - Computer Science", "description": qcc})
    assert not is_qcc_math_teaching({"title": "Assistant Professor - Mathematics and Computer Science",
                                     "description": qcc})
    feed = {"jobs": [job(url="https://example.org/qcc", org="CUNY Queensborough Community College",
                         title="Adjunct Lecturer - Mathematics (College Now Program)", teaching_lead=True,
                         salary_max=None, description="Responsibilities: Teach MA 119. Hourly.")]}
    out = screen(feed, now=NOW)
    assert out["candidates"] == [] and out["dropped"] == []
    assert out["teaching_lead_count"] == 1
    assert out["teaching_leads"][0]["flags"] == ["College Now: teaches high school students in college courses"]


def test_collect_keeps_qcc_math_postings_the_title_filter_would_drop(monkeypatch):
    from collector import sources
    posts = [
        {"title": "Adjunct Lecturer - Mathematics and Computer Science Department", "location": "Queens, NY",
         "url": "https://cuny.jobs/qcc-math", "remote": None, "posted": "2026-11-26", "department": None,
         "employment_type": None, "salary_min": None, "salary_max": None, "salary_text": None,
         "description": "Queensborough Community College invites applications.", "org": "CUNY Queensborough Community College"},
        {"title": "Adjunct Lecturer - English", "location": "Queens, NY", "url": "https://cuny.jobs/qcc-eng",
         "remote": None, "posted": "2026-11-26", "department": None, "employment_type": None, "salary_min": None,
         "salary_max": None, "salary_text": None, "description": "Queensborough Community College.",
         "org": "CUNY Queensborough Community College"},
    ]
    monkeypatch.setitem(collect.ADAPTERS, "fake_cuny", lambda token: [dict(p) for p in posts])
    org = {"name": "CUNY (cuny.jobs)", "category": "Higher Ed", "tier": "2", "ats": "fake_cuny", "token": "x",
           "careers_url": ""}
    monkeypatch.setattr(collect, "resolve_platform", lambda org, cache: ("fake_cuny", "x", "orgs.csv"))
    _, jobs = collect.collect_org(org, {}, "2026-11-26", {})
    assert [j["url"] for j in jobs] == ["https://cuny.jobs/qcc-math"]
    assert jobs[0]["teaching_lead"] is True and jobs[0]["org"] == "CUNY Queensborough Community College"
