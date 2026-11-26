from pathlib import Path

from collector import sources, sources_ny
from collector.text import extract_salary

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text()


def test_statejobs_table_rows_map_counties_to_places():
    jobs = sources_ny.parse_statejobs_table(read("statejobs_table.html"))
    assert len(jobs) == 4
    kings = jobs[0]
    assert kings["title"] == "Senior Project Manager"
    assert kings["location"] == "Brooklyn, NY"
    assert kings["posted"] == "2026-11-25" and kings["extra"]["post_until"] == "2026-12-31"
    assert kings["org"].startswith("NYS DASNY")
    assert kings["_detail"] == ("statejobsny", "229635")
    erie = jobs[-1]
    assert erie["location"] == "Erie County, NY (New York State)"
    assert erie["title"] == "Trades Specialist Plumber & Steamfitter"


def test_statejobs_detail_salary_is_readable():
    text = sources_ny.statejobs_detail_text(read("statejobs_detail.html"))
    assert text.startswith("Date Posted")
    assert "Telecommuting allowed? No" in text
    assert extract_salary(text)[:2] == (52694.0, 64321.0)


def test_cuny_rss_titles_carry_the_place():
    jobs = sources_ny.parse_cuny_rss(read("cuny.rss"))
    assert jobs[0]["title"] == "Employee Relations Manager, Human Resources"
    assert jobs[0]["location"] == "Queens, NY"
    assert jobs[0]["org"] == "CUNY"
    assert jobs[0]["posted"] == "2026-11-26"
    assert len(jobs[0]["description"]) > 500


def test_jobboard_rss_splits_role_employer_and_place():
    jobs = sources_ny.parse_jobboard_rss(read("jobboard.rss"))
    first, second = jobs[0], jobs[1]
    assert first["title"] == "Assistant Business Manager (Staten Island)"
    assert first["org"] == "YMCA of Greater New York"
    assert first["location"] == "New York City"
    assert first["salary_max"] == 77000
    assert second["title"] == "Housing Budget and Policy Analyst"
    assert second["org"] == "New York City Independent Budget Office"
    assert first["_detail"] == ("page", first["url"])


def test_ag_postings():
    jobs = sources_ny.parse_ag_page(read("ag_postings.html"))
    titles = [j["title"] for j in jobs]
    assert "Research Analyst: AI Safety, Standards, Enforcement, and Transparency Unit" in titles
    ra = jobs[titles.index("Research Analyst: AI Safety, Standards, Enforcement, and Transparency Unit")]
    assert ra["location"] == "New York City, NY"
    assert ra["extra"]["post_until"] == "2026-12-11"
    assert ra["url"].startswith("https://ag.ny.gov/sites/default/files/") and ra["url"].endswith(".pdf")
    assert ra["org"] == "NYS Office of the Attorney General"


def test_new_readers_are_registered():
    for name in ("statejobsny", "cuny_rss", "jobboard_rss", "nys_ag"):
        assert name in sources.ADAPTERS
