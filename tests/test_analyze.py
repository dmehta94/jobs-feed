'''Tests for the posting analysis.'''
from collector import analyze


def job(title, desc, **kw):
    return {"title": title, "description": desc, "org": kw.get("org", "Org"), "years_required": kw.get("years"),
            "salary_max": kw.get("pay"), "phd_required": kw.get("phd", False)}


def test_families():
    assert analyze.family_of({"title": "Psychometrician II"}) == "Assessment & measurement"
    assert analyze.family_of({"title": "Institutional Research Analyst"}) == "Institutional research"
    assert analyze.family_of({"title": "Research Associate, Education"}) == "Policy & social research"
    assert analyze.family_of({"title": "Data Analyst"}) == "General data analysis"
    assert analyze.family_of({"title": "Office Manager"}) == "Other"


def test_mentions_language_r_without_false_positives():
    assert "R" in analyze.mentions(job("Analyst", "Experience with R, Python, or Stata."))
    assert "R" in analyze.mentions(job("Analyst", "Proficiency in R"))
    assert "R" not in analyze.mentions(job("Analyst", "R&D team. Our R-squared values. Research"))
    assert {"SQL", "Python", "Stata"} <= analyze.mentions(job("Analyst", "SQL, Python and Stata"))
    assert "AI / LLM tools" in analyze.mentions(job("Analyst", "Comfort using generative AI tools"))
    assert "AI / LLM tools" not in analyze.mentions(job("Analyst", "Said she'd maintain the data"))


def test_summarize_and_markdown():
    jobs = [
        job("Data Analyst", "SQL and Python. Write reports.", years=2, pay=90000),
        job("Research Associate", "Stata, regression, causal inference.", years=3, pay=85000, phd=False),
        job("Psychometrician", "IRT and reliability. PhD required.", years=1, phd=True),
        job("Data Analyst", ""),  # no description: skipped
    ]
    s = analyze.summarize(jobs)
    assert s["postings_with_descriptions"] == 3
    assert s["overall"]["term_share_pct"]["SQL"] == 33
    assert s["overall"]["years_required"]["median"] == 2
    assert s["families"]["Assessment & measurement"]["phd_required_pct"] == 100
    md = analyze.to_markdown(s, "2026-10-19")
    assert "## All postings (3 postings)" in md and "| SQL | 33% |" in md


def test_writing_ignores_reporting_lines():
    assert "Writing / reports" not in analyze.mentions(job("Analyst", "This role reports to the Director."))
    assert "Writing / reports" in analyze.mentions(job("Analyst", "Write clear research briefs."))
