from collector import alerts, email_digest

ALERT = """Your job alert for New York City Metropolitan AreaManage your job alerts: https://www.linkedin.com/comm/jobs/alerts?otpToken=SECRET

Data Scientist, M-Powering Teacher Coaching
Harvard Graduate School of Education
Cambridge

This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/4473978429/?trackingId=abc&otpToken=SECRET

---------------------------------------------------------

Associate Data Scientist
Billtrust
Hamilton Township
View job: https://www.linkedin.com/comm/jobs/view/4434363514/?otpToken=SECRET

---------------------------------------------------------

Research Analyst
Example Charter Schools
New York, NY
View job: https://www.linkedin.com/comm/jobs/view/1111/?otpToken=SECRET

---------------------------------------------------------

Data Analyst
Some University
Reston, VA
View job: https://www.linkedin.com/comm/jobs/view/2222/?otpToken=SECRET

---------------------------------------------------------

Education Data Analyst
Learning Nonprofit
United States (Remote)
$85K-$95K / year
View job: https://www.linkedin.com/comm/jobs/view/3333/?otpToken=SECRET

See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search-results/?otpToken=SECRET
"""


def test_parse_alert_reads_postings_and_drops_tokens():
    jobs = alerts.parse_alert(ALERT)
    assert [j["title"] for j in jobs][:2] == ["Data Scientist, M-Powering Teacher Coaching", "Associate Data Scientist"]
    first = jobs[0]
    assert first["org"] == "Harvard Graduate School of Education" and first["location"] == "Cambridge"
    assert first["url"] == "https://www.linkedin.com/jobs/view/4473978429/"
    assert all("SECRET" not in str(j) for j in jobs)
    last = jobs[-1]
    assert last["remote"] is True and (last["salary_min"], last["salary_max"]) == (85000, 95000)


def test_pick_alerts_keeps_mission_fits_only():
    picked = alerts.pick_alerts([ALERT, ALERT], feed_jobs=[])
    titles = [(j["org"], j["title"]) for j in picked]
    assert ("Harvard Graduate School of Education", "Data Scientist, M-Powering Teacher Coaching") in titles
    assert ("Learning Nonprofit", "Education Data Analyst") in titles
    orgs = {j["org"] for j in picked}
    assert "Billtrust" not in orgs                # no mission signal
    assert "Example Charter Schools" not in orgs  # charter
    assert "Some University" not in orgs          # on-site elsewhere
    assert len(titles) == len(set(titles))        # the repeated email adds nothing


def test_pick_alerts_skips_postings_already_collected():
    feed = [{"org": "Harvard University", "title": "Data Scientist, M-Powering Teacher Coaching"}]
    picked = alerts.pick_alerts([ALERT], feed_jobs=feed)
    assert all("M-Powering" not in j["title"] for j in picked)


def test_email_lists_alerts_in_their_own_section():
    feed = {"jobs": [], "sources_ok": 1, "sources_total": 1}
    picked = alerts.pick_alerts([ALERT], feed_jobs=[])
    subject, text, html_body = email_digest.build(feed, [], {"sources": []}, alerts=picked)
    assert "from LinkedIn" in subject
    assert "FROM YOUR LINKEDIN ALERTS" in text and "Nothing new this week." not in text
    assert "SECRET" not in text + html_body


def test_read_mailbox_skipped_without_secrets(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert alerts.read_mailbox() == []
