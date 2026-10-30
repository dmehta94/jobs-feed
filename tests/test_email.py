'''Tests for the weekly email (no real email is sent).'''
from datetime import date

from collector import email_digest

TODAY = date(2026, 10, 19)


def job(**kw):
    base = {"org": "Zearn", "title": "Data Analyst", "tier": 1, "category": "Education", "url": "https://x/1",
            "location": "New York, NY", "remote": None, "new_this_week": True, "posted": "2026-10-15",
            "salary_min": 85000, "salary_max": 95000, "years_required": 2, "phd_required": False,
            "description": "Analyze student data."}
    base.update(kw)
    return base


def test_select_applies_filters(monkeypatch):
    monkeypatch.setenv("MIN_SALARY", "80000")
    monkeypatch.setenv("EXCLUDE_TERMS", "charter school, hedge fund")
    feed = {"jobs": [
        job(),
        job(title="Old", new_this_week=False),
        job(title="Low pay", salary_min=50000, salary_max=60000),
        job(title="Unknown pay", salary_min=None, salary_max=None),
        job(title="PhD", phd_required=True),
        job(title="Masters", advanced_degree_required="Master's"),
        job(title="Senior", years_required=7),
        job(title="Charter", description="Join our charter school network"),
    ]}
    assert [j["title"] for j in email_digest.select(feed, TODAY)] == ["Data Analyst", "Unknown pay"]


def test_first_run_uses_posted_date(monkeypatch):
    monkeypatch.delenv("MIN_SALARY", raising=False)
    monkeypatch.delenv("EXCLUDE_TERMS", raising=False)
    feed = {"first_run": True, "jobs": [job(posted="2026-10-10"), job(title="Stale", posted="2026-08-01"),
                                        job(title="Undated", posted=None)]}
    assert [j["title"] for j in email_digest.select(feed, TODAY)] == ["Data Analyst", "Undated"]


def test_build_groups_and_flags_broken_sources():
    jobs = [job(), job(org="Dept Of Education", tier=2, category="NYC Government", exam_required=True,
                       post_until="2026-10-30", salary_min=None, salary_max=None)]
    feed = {"sources_ok": 60, "sources_total": 71}
    health = {"sources": [{"org": "MDRC", "tier": 1, "status": "no supported platform found"},
                          {"org": "Zearn", "tier": 1, "status": "ok"}]}
    subject, text, html_body = email_digest.build(feed, jobs, health, TODAY)
    assert subject == "Job feed — Oct 19: top 2 new postings"
    assert "TIER 1 ORGANIZATIONS" in text and "NYC GOVERNMENT" in text
    assert "$85,000–$95,000" in text and "pay not listed" in text
    assert "civil service exam" in text and "closes 2026-10-30" in text
    assert "Not reading: MDRC." in text
    assert "<a href='https://x/1'>Data Analyst</a>" in html_body


def test_build_empty_week():
    subject, text, _ = email_digest.build({"sources_ok": 1, "sources_total": 1}, [], {"sources": []}, TODAY)
    assert "top 0 new postings" in subject and "Nothing new this week." in text


def test_main_skips_without_secrets(monkeypatch, capsys):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert email_digest.main() == 0
    assert "Email skipped" in capsys.readouterr().out


def test_send_uses_gmail_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = (host, port)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, user, pw):
            sent["login"] = (user, pw)
        def sendmail(self, frm, to, msg):
            sent["to"] = to
            sent["msg"] = msg

    monkeypatch.setattr(email_digest.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass")
    monkeypatch.delenv("DIGEST_TO", raising=False)
    email_digest.send("Subject", "text", "<p>html</p>")
    assert sent["host"] == ("smtp.gmail.com", 465)
    assert sent["login"] == ("me@example.com", "app-pass")
    assert sent["to"] == ["me@example.com"]
    assert "Subject: Subject" in sent["msg"]


def test_exclude_employers_matches_employer_only(monkeypatch):
    monkeypatch.delenv("MIN_SALARY", raising=False)
    monkeypatch.delenv("EXCLUDE_TERMS", raising=False)
    monkeypatch.setenv("EXCLUDE_EMPLOYERS", "OpenAI, Meta")
    feed = {"jobs": [job(org="OpenAI", title="Education Analyst"),
                     job(org="Meta Platforms", title="Data Analyst"),
                     job(org="Newsela", title="Data Analyst", description="Builds on the OpenAI API")]}
    assert [j["org"] for j in email_digest.select(feed, TODAY)] == ["Newsela"]


def test_exclude_employers_exemption(monkeypatch):
    monkeypatch.delenv("MIN_SALARY", raising=False)
    monkeypatch.delenv("EXCLUDE_TERMS", raising=False)
    monkeypatch.setenv("EXCLUDE_EMPLOYERS", "Google, !DeepMind")
    feed = {"jobs": [job(org="Google", title="Data Analyst"), job(org="Google DeepMind", title="Research Analyst")]}
    assert [j["org"] for j in email_digest.select(feed, TODAY)] == ["Google DeepMind"]


from collector import fit


def test_fit_drops_senior_and_off_target_titles():
    for title in ["Senior Data Analyst", "Director of Research", "Financial Analyst", "Mainframe Programmer Analyst",
                  "Business Analyst (Remote)", "Data Engineer", "UiPath Analyst & Designer", "Technical Program Manager",
                  "Pool Supervisor", "Data Analytics Intern"]:
        assert fit.score(job(title=title))[0] is None, title


def test_fit_ranks_education_entry_level_first():
    edu = job(org="New Visions", title="Data Analyst (Entry-Level)", category="Education", years_required=1,
              description="Support schools and students with data.")
    nyc = job(org="Dept Of Transportation", title="Data Engagement Analyst", category="NYC Government", tier=2,
              years_required=3, description="Minimum qualifications: A baccalaureate degree from an accredited college.")
    assert fit.score(edu)[0] > fit.score(nyc)[0]
    assert "education focus" not in fit.score(nyc)[1]  # degree boilerplate isn't an education focus


def test_fit_limits_ai_labs_to_education_and_societal_roles():
    assert fit.score(job(org="Anthropic", category="AI & Tech", title="Research Engineer, RL"))[0] is None
    assert fit.score(job(org="Anthropic", category="AI & Tech", title="Economic Research Analyst"))[0] is not None


def test_shortlist_caps():
    jobs = [job(org="Big Org", title=f"Data Analyst {i}", url=f"u{i}") for i in range(6)]
    jobs += [job(org=f"Agency {i}", title="Research Analyst", category="NYC Government", url=f"n{i}") for i in range(8)]
    out, matched = fit.shortlist(jobs, max_items=10, max_per_org=2, max_nyc=3, min_score=0)
    assert matched == 14
    assert sum(j["org"] == "Big Org" for j in out) == 2
    assert sum(j["category"] == "NYC Government" for j in out) == 3


def _run_main(monkeypatch, tmp_path, dry_run, sent):
    (tmp_path / "jobs.json").write_text('{"jobs": []}')
    (tmp_path / "health.json").write_text('{"sources": []}')
    monkeypatch.setattr(email_digest, "DATA", tmp_path)
    monkeypatch.setattr(email_digest, "send", lambda *a: sent.append(a))
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("READ_LINKEDIN_ALERTS", "no")
    if dry_run:
        monkeypatch.setenv("DRY_RUN", "1")
    else:
        monkeypatch.delenv("DRY_RUN", raising=False)
    assert email_digest.main() == 0
    import json
    return json.loads((tmp_path / "email_status.json").read_text())


def test_last_sent_recorded_on_send_and_kept_on_dry_run(monkeypatch, tmp_path):
    (tmp_path / "email_status.json").write_text('{"last_sent": "2026-10-26"}')
    sent = []
    status = _run_main(monkeypatch, tmp_path, dry_run=True, sent=sent)
    assert status["last_sent"] == "2026-10-26" and not sent
    status = _run_main(monkeypatch, tmp_path, dry_run=False, sent=sent)
    assert status["last_sent"] == date.today().isoformat() and len(sent) == 1
